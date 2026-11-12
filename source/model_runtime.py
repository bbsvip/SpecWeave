"""Direct, bounded streaming transport for SpecWeave's API-key models."""
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from chat_events import normalize_usage
from security import decrypt


class ModelError(HTTPException):
    def __init__(self, detail, status=502):
        super().__init__(status, detail)

    def __str__(self):
        return self.detail


def require_model(model):
    if model.provider.kind == 'openai-oauth':
        raise ModelError('Model ChatGPT OAuth đã ngừng hỗ trợ. Chọn model dùng API key trong Project Settings.', 409)
    if not model.enabled or not model.provider.enabled:
        raise ModelError('Model hoặc provider đã bị vô hiệu hóa', 409)


def snapshot_model(model):
    """Hold the selected endpoint and settings constant for one durable run."""
    require_model(model)
    provider = model.provider
    return SimpleNamespace(
        model_id=model.model_id, api=model.api, context_window=model.context_window,
        max_output_tokens=model.max_output_tokens, reasoning=model.reasoning,
        compat=dict(model.compat or {}), sampling_params=dict(model.sampling_params or {}), enabled=True,
        provider=SimpleNamespace(kind=provider.kind, base_url=provider.base_url,
                                 encrypted_key=provider.encrypted_key, enabled=True),
    )


def endpoint(base, resource):
    base = base.rstrip('/')
    try:
        url = urlsplit(base)
    except ValueError as exc:
        raise ModelError('Base URL của provider không hợp lệ', 422) from exc
    if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.query or url.fragment:
        raise ModelError('Base URL phải dùng HTTP(S) và không chứa thông tin xác thực, query hoặc fragment', 422)
    for suffix in ('/chat/completions', '/responses', '/models'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    if not urlsplit(base).path:
        base += '/v1'
    return f'{base}/{resource}'


def budget_messages(system, prompt, history, *, context_window, output_budget):
    """Pack recent whole exchanges; reserve room for output and protocol overhead.

    The byte budget is conservative for UTF-8 models without a tokenizer API.
    The current request and system instructions are never silently truncated.
    """
    messages = [{'role':'system','content':system}, {'role':'user','content':prompt}]
    remaining = context_window - output_budget - 96 - sum(message_size(m) for m in messages)
    if remaining < 0:
        raise ModelError('Nội dung hiện tại vượt context đã cấu hình. Hãy rút ngắn tin nhắn hoặc tăng context.', 422)
    recent = []
    for message in reversed(history):
        cost = message_size(message)
        if cost > remaining:
            break
        remaining -= cost
        recent.append(message)
    while recent and recent[-1]['role'] != 'user':
        recent.pop()
    return [messages[0], *reversed(recent), messages[-1]]


def message_size(message):
    return len(json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 12


@dataclass
class ModelTurn:
    text: str = ''
    reasoning: str = ''
    calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    reason: str = ''


def tool_definition(tool, *, strict):
    function = dict(tool['function'])
    function['strict'] = strict
    if strict:
        schema = function['parameters']
        required = set(schema.get('required', []))
        props = {name:(spec if name in required else {**spec,'type':[spec['type'],'null']})
                 for name,spec in schema['properties'].items()}
        function['parameters'] = {**schema,'properties':props,'required':list(props),'additionalProperties':False}
    return function


def request_body(model, messages, *, max_tokens, tools, thinking, response_items=None):
    compat = model.compat or {}
    sampling = model.sampling_params or {}
    api = model.api or 'openai-completions'
    body = {'model':model.model_id, 'stream':True}
    strict = compat.get('supportsStrictMode') is True
    def roles(items):
        if compat.get('supportsDeveloperRole'):
            return [{**item,'role':'developer'} if item.get('role')=='system' else item for item in items]
        return items
    if api == 'openai-responses':
        body.update(input=roles(response_items if response_items is not None else messages),
                    max_output_tokens=max_tokens, store=False)
        body.update({k:v for k,v in sampling.items() if k in ('temperature','top_p')})
        if model.reasoning and thinking != 'off':
            body['reasoning'] = {'effort': thinking_effort(thinking)}
        if tools:
            body['tools'] = [{'type':'function', **tool_definition(tool,strict=strict)} for tool in tools]
        return 'responses', body
    if api != 'openai-completions':
        raise ModelError('Giao thức model không được hỗ trợ', 422)
    body.update(sampling)
    default_limit = 'max_completion_tokens' if urlsplit(model.provider.base_url).hostname == 'api.openai.com' else 'max_tokens'
    body[compat.get('maxTokensField',default_limit)] = max_tokens
    body['messages'] = roles(messages)
    if compat.get('supportsUsageInStreaming', True):
        body['stream_options'] = {'include_usage':True}
    if compat.get('supportsStore'):
        body['store'] = False
    if tools:
        body['tools'] = [{'type':'function','function':tool_definition(tool,strict=True)}
                         for tool in tools] if strict else tools
    if model.reasoning:
        apply_thinking(body, compat, thinking)
    return 'chat/completions', body


def thinking_effort(level):
    return level if level in ('low','medium','high') else 'low' if level in ('minimal','off') else 'high'


def apply_thinking(body, compat, level):
    style = compat.get('thinkingFormat')
    active = level != 'off'
    if style in ('qwen-chat-template','chat-template'):
        body['chat_template_kwargs'] = {'enable_thinking':active}
    elif style == 'qwen':
        body['enable_thinking'] = active
    elif style in ('deepseek','zai'):
        body['thinking'] = {'type':'enabled' if active else 'disabled'}
    elif style == 'openrouter':
        body['reasoning'] = {'enabled':active}
        if active:
            body['reasoning']['effort'] = thinking_effort(level)
    elif style == 'string-thinking':
        body['thinking'] = 'enabled' if active else 'disabled'
    elif style in ('together','baseten','ant-ling'):
        body['reasoning'] = {'effort':thinking_effort(level)} if active else {'enabled':False}
    elif compat.get('supportsReasoningEffort') or style == 'openai':
        if active:
            body['reasoning_effort'] = thinking_effort(level)
    field = compat.get('thinkingTokenBudgetField')
    if field and active:
        body[field] = {'minimal':256,'low':512,'medium':1024,'high':2048}.get(level, 4096)


async def sse_data(response):
    data = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield '\n'.join(data)
            data, size = [], 0
        elif line.startswith('data:'):
            size += len(line)
            if size > 262144:
                raise ModelError('Provider gửi một sự kiện quá lớn')
            data.append(line[5:].lstrip(' '))
    if data:
        yield '\n'.join(data)


def append_chat_calls(calls, pieces):
    for piece in pieces:
        index = piece.get('index')
        if type(index) is not int or not 0 <= index < 8:
            raise ModelError('Provider gửi chỉ số tool call không hợp lệ')
        call = calls.setdefault(index, {'id':'','name':'','arguments':''})
        function = piece.get('function') or {}
        call['id'] += piece.get('id') or ''
        call['name'] += function.get('name') or ''
        call['arguments'] += function.get('arguments') or ''
        if len(call['arguments']) > 64000 or len(call['name']) > 120 or len(call['id']) > 180:
            raise ModelError('Tham số công cụ vượt giới hạn')


async def stream_turn(client, model, messages, *, max_tokens, thinking, tools=(), response_items=None):
    """Yield text/thinking deltas and exactly one validated terminal ModelTurn."""
    require_model(model)
    resource, body = request_body(model, messages, max_tokens=max_tokens, tools=tools,
                                  thinking=thinking, response_items=response_items)
    url = endpoint(model.provider.base_url, resource)
    key = decrypt(model.provider.encrypted_key) if model.provider.encrypted_key else ''
    headers = {'Accept':'text/event-stream'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    turn = ModelTurn()
    calls = {}
    chunks = {'delta':[], 'thinking':[]}
    received = 0
    pending_kind, pending = None, ''
    try:
        async with client.stream('POST', url, json=body, headers=headers) as response:
            response.raise_for_status()
            if 'text/event-stream' not in response.headers.get('content-type','').lower():
                raise ModelError('Provider không trả về stream SSE')
            async for raw in sse_data(response):
                if raw.strip() == '[DONE]':
                    break
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get('error'):
                    raise ModelError('Provider báo lỗi trong luồng trả lời')
                parts = []
                if resource == 'chat/completions':
                    if data.get('usage'):
                        turn.usage = normalize_usage(data['usage'], source='provider')
                    for choice in data.get('choices') or []:
                        if choice.get('index',0) != 0:
                            continue
                        delta = choice.get('delta') or {}
                        parts.extend([('thinking',delta.get('reasoning_content') or delta.get('reasoning')),
                                      ('delta',delta.get('content'))])
                        append_chat_calls(calls, delta.get('tool_calls') or [])
                        turn.reason = choice.get('finish_reason') or turn.reason
                else:
                    kind = data.get('type')
                    if kind == 'response.output_text.delta':
                        parts.append(('delta',data.get('delta')))
                    elif kind in ('response.reasoning_text.delta','response.reasoning_summary_text.delta'):
                        parts.append(('thinking',data.get('delta')))
                    elif kind in ('response.failed','error'):
                        raise ModelError('Provider không hoàn tất yêu cầu Responses')
                    elif kind in ('response.completed','response.incomplete'):
                        result = data.get('response') or {}
                        turn.usage = normalize_usage(result.get('usage'), source='provider')
                        turn.reason = 'stop' if kind == 'response.completed' else (
                            'length' if (result.get('incomplete_details') or {}).get('reason') == 'max_output_tokens'
                            else 'incomplete')
                        for item in result.get('output') or []:
                            if item.get('type') == 'function_call':
                                if len(calls) == 8 or len(item.get('arguments','')) > 64000:
                                    raise ModelError('Provider gửi quá nhiều tham số công cụ')
                                calls[len(calls)] = {'id':item['call_id'],'name':item['name'],'arguments':item['arguments']}
                for kind, text in parts:
                    if not isinstance(text, str) or not text:
                        continue
                    first_delta = received == 0
                    received += len(text)
                    if received > 300000:
                        raise ModelError('Phản hồi vượt giới hạn an toàn của ứng dụng')
                    chunks[kind].append(text)
                    if pending and pending_kind != kind:
                        yield pending_kind, {'text':pending}
                        pending = ''
                    pending_kind = kind
                    pending += text
                    if first_delta or len(pending) >= 96 or '\n' in pending:
                        yield pending_kind, {'text':pending}
                        pending = ''
                if resource == 'responses' and turn.reason:
                    break
    except httpx.HTTPStatusError as exc:
        raise ModelError(f'Provider từ chối yêu cầu (HTTP {exc.response.status_code})') from exc
    except httpx.HTTPError as exc:
        raise ModelError('Mất kết nối hoặc hết thời gian chờ provider') from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ModelError('Provider gửi dữ liệu stream không hợp lệ') from exc
    if not turn.reason:
        raise ModelError('Luồng model kết thúc trước sự kiện hoàn tất')
    if pending:
        yield pending_kind, {'text':pending}
    turn.text = ''.join(chunks['delta'])
    turn.reasoning = ''.join(chunks['thinking'])
    turn.calls = [calls[i] for i in sorted(calls)]
    yield 'result', turn


def model_client():
    return httpx.AsyncClient(timeout=httpx.Timeout(240, connect=12), trust_env=False, follow_redirects=False)


async def complete(project, model, actor_id, *, system_context, message, discussion_id=None, max_tokens=None):
    """One-shot planning and review completion through the same native transport."""
    require_model(model)
    limit = min(model.max_output_tokens, max_tokens or 4096, model.context_window // 2)
    messages = budget_messages(system_context, message, [], context_window=model.context_window, output_budget=limit)
    async with model_client() as client:
        async for kind, result in stream_turn(client,model,messages,max_tokens=limit,thinking='off'):
            if kind == 'result':
                if result.reason != 'stop' or result.calls or not result.text.strip():
                    raise ModelError('Model chưa trả về kết quả hoàn chỉnh')
                return {'text':result.text,'usage':result.usage}
    raise ModelError('Model chưa trả kết quả')
