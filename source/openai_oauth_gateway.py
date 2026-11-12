"""OpenAI-only, owner-scoped Pi OAuth routing. Zero OpenAI credentials in application DB.

DO NOT use this adapter for non-OpenAI providers. Explicitly authorized calls only;
not a general-purpose API gateway. OAuth is opt-in for locally self-hosted deployments.
"""
import os
import httpx
from fastapi import HTTPException


def enabled():
    return os.environ.get('OPENAI_OAUTH_LOCAL_MODE','').lower() in ('1','true','yes')


def require_access(project, model, actor_id):
    if model.provider.kind!='openai-oauth':return
    if not enabled():
        raise HTTPException(503,'OpenAI OAuth is disabled (local owner-controlled mode only)')
    if (not actor_id or project.owner_id!=actor_id or model.provider.owner_id!=actor_id):
        raise HTTPException(403,'OpenAI account is private to its owner; no subscription sharing')
    if not model.provider.enabled or not model.enabled:
        raise HTTPException(409,'OpenAI OAuth provider/model disabled')


def runner_config():
    url=os.environ.get('PI_RUNNER_URL','').rstrip('/')
    key=os.environ.get('PI_RUNNER_KEY','')
    if not url or len(key)<32:
        raise HTTPException(503,'A Pi SDK Agent Host and a strong PI_RUNNER_KEY are required for OpenAI OAuth')
    return url,{'X-Runner-Key':key}


async def relay(method,path,body=None,timeout=30):
    """Only callers control path; never relay OAuth tokens to frontend or API logs."""
    if not path.startswith('/oauth/openai/') and path!='/oauth/openai/status':
        raise HTTPException(400,'Unsupported runner OAuth route')
    url,headers=runner_config()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout,connect=8),trust_env=False) as client:
            response=await client.request(method,url+path,headers=headers,json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(502,'Local OpenAI OAuth runtime unavailable') from exc
    try:result=response.json()
    except ValueError:result={}
    if response.status_code>=400:
        detail=result.get('detail','OAuth runtime request failed')
        if response.status_code in (400,401,403,404,409,422,503,504):
            raise HTTPException(response.status_code,str(detail)[:250])
        raise HTTPException(502,'OpenAI OAuth runtime returned an error')
    return result


