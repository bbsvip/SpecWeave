"""Transport ONLY: SpecWeave -> in-process Pi SDK Agent Host.
This module does not assemble an agent, call LLM APIs or implement a tool loop.
"""
import json
import httpx
from fastapi import HTTPException
from chat_coordination import ChatBusy
from security import decrypt
from openai_oauth_gateway import require_access, runner_config


def model_payload(project, model, actor_id=None):
    require_access(project, model, actor_id)
    return {
        'project_id': project.id,
        'provider_base': model.provider.base_url,
        'provider_key': '' if model.provider.kind=='openai-oauth' else decrypt(model.provider.encrypted_key),
        'auth_mode': 'openai-oauth' if model.provider.kind=='openai-oauth' else 'api_key',
        'model_id': model.model_id,
        'reasoning': bool(model.reasoning),
        'context_window': model.context_window,
        'thinking': project.thinking_level,
    }


async def complete(project, model, actor_id, *, system_context, message, discussion_id=None, max_tokens=None):
    """Stateless Pi session for a single structured planning or review task."""
    url, headers = runner_config()
    payload = model_payload(project, model, actor_id)
    payload.update({'discussion_id':discussion_id or project.id,
                    'system_context':str(system_context)[:30000],
                    'message':str(message)[:125000]})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=12),trust_env=False) as client:
            response=await client.post(url+'/complete',json=payload,headers=headers)
            response.raise_for_status()
            result=response.json()
    except httpx.HTTPStatusError as exc:
        # Never include provider HTTP response text: it may contain credentials or prompts.
        raise HTTPException(502, f'Pi Agent Host rejected completion (HTTP {exc.response.status_code})') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, 'Pi Agent Host unavailable') from exc
    if not isinstance(result,dict) or not isinstance(result.get('text'),str):
        raise HTTPException(502, 'Pi returned an invalid completion')
    return result


async def stream(project, model, actor_id, payload):
    url,headers=runner_config()
    payload={**model_payload(project,model,actor_id),**payload}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(540,connect=15,read=540),trust_env=False) as client:
            async with client.stream('POST',url+'/run',json=payload,headers=headers) as response:
                if response.status_code==409:
                    await response.aread()
                    try: code=response.json().get('code')
                    except (ValueError, AttributeError): code=None
                    if code=='PI_SESSION_BUSY':
                        raise ChatBusy()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith('data: '):continue
                    try: event=json.loads(line[6:])
                    except ValueError:continue
                    if isinstance(event,dict) and isinstance(event.get('payload'),dict):
                        kind=event.get('type','status')
                        yield kind,event['payload']
                        if kind in ('final','error'):
                            return
                raise HTTPException(502,'Pi Agent Host stream ended before a final event')
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502,f'Pi Agent Host rejected run (HTTP {exc.response.status_code})') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502,'Pi Agent Host stream disconnected') from exc


async def abort(project_id, discussion_id, run_id):
    try:
        url, headers = runner_config()
        async with httpx.AsyncClient(timeout=18,trust_env=False) as client:
            response=await client.post(url+'/abort',json={
                'project_id':project_id,'discussion_id':discussion_id,'run_id':run_id},headers=headers)
            return response.status_code==200
    except (httpx.HTTPError, HTTPException):
        return False
