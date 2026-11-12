"""Validate the custom model settings exposed by provider management."""
import json
import math

from fastapi import HTTPException

CUSTOM_FIELDS = {'max_output_tokens', 'api', 'compat', 'sampling_params'}
COMPAT_BOOLEANS = {
    'supportsStore', 'supportsDeveloperRole', 'supportsReasoningEffort',
    'supportsUsageInStreaming', 'supportsStrictMode', 'requiresToolResultName',
    'requiresAssistantAfterToolResult', 'requiresNonNullAssistantContent',
    'requiresThinkingAsText', 'requiresReasoningContentOnAssistantMessages',
}
COMPAT_CHOICES = {
    'maxTokensField': {'max_tokens', 'max_completion_tokens'},
    'thinkingFormat': {'openai', 'openrouter', 'deepseek', 'together', 'baseten',
                       'zai', 'qwen', 'qwen-chat-template', 'string-thinking'},
    'thinkingTokenBudgetField': {'thinking_token_budget', 'thinking_budget', 'thinking_budget_tokens'},
}
SAMPLING_RANGES = {
    'temperature': (0, 2), 'top_p': (0, 1), 'top_k': (1, 1000000),
    'min_p': (0, 1), 'repetition_penalty': (0, 10),
    'presence_penalty': (-2, 2), 'frequency_penalty': (-2, 2),
    'seed': (-2147483648, 2147483647),
}


def integer_setting(value, name, minimum, maximum):
    if type(value) is str:
        try:
            value = int(value)
        except ValueError:
            raise HTTPException(422, f'{name} phải là số nguyên')
    if type(value) is not int or not minimum <= value <= maximum:
        raise HTTPException(422, f'{name} phải trong khoảng {minimum}–{maximum}')
    return value


def json_settings(value, name):
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 16000:
        raise HTTPException(422, f'{name} phải là JSON object tối đa 16 KB')
    return dict(value)


def parse_model_settings(data, *, model=None, custom=True):
    if not isinstance(data, dict):
        raise HTTPException(422, 'Cấu hình model phải là object')
    if not custom and CUSTOM_FIELDS.intersection(data):
        raise HTTPException(422, 'Cấu hình nâng cao chỉ áp dụng cho model custom dùng API key')
    settings = {}
    if 'reasoning' in data:
        if type(data['reasoning']) is not bool:
            raise HTTPException(422, 'Reasoning phải là boolean')
        settings['reasoning'] = data['reasoning']
    if 'context_window' in data:
        settings['context_window'] = integer_setting(data['context_window'], 'Context window', 1024, 2097152)
    if 'max_output_tokens' in data:
        settings['max_output_tokens'] = integer_setting(data['max_output_tokens'], 'Max output tokens', 128, 2097152)
    context = settings.get('context_window', model.context_window if model else 32768)
    output = settings.get('max_output_tokens', model.max_output_tokens if model else 8192)
    if custom and {'context_window', 'max_output_tokens'}.intersection(data) and output >= context:
        raise HTTPException(422, 'Max output tokens phải nhỏ hơn context window')
    api = data.get('api', model.api if model else 'openai-completions')
    if custom and api not in ('openai-completions', 'openai-responses'):
        raise HTTPException(422, 'API phải là openai-completions hoặc openai-responses')
    if 'api' in data:
        settings['api'] = api
    if custom and ('compat' in data or 'api' in data):
        compat = json_settings(data.get('compat', model.compat if model else {}), 'Compatibility')
        for key, value in compat.items():
            if api == 'openai-responses' and key not in ('supportsDeveloperRole', 'supportsStrictMode'):
                raise HTTPException(422, f'Compatibility {key} không hỗ trợ API Responses')
            if key in COMPAT_BOOLEANS:
                if type(value) is not bool:
                    raise HTTPException(422, f'Compatibility {key} phải là boolean')
            elif key in COMPAT_CHOICES:
                if not isinstance(value, str) or value not in COMPAT_CHOICES[key]:
                    raise HTTPException(422, f'Giá trị Compatibility {key} không hợp lệ')
            else:
                raise HTTPException(422, f'Compatibility không hỗ trợ trường {key}')
        settings['compat'] = compat
    if 'sampling_params' in data:
        sampling = json_settings(data['sampling_params'], 'Sampling parameters')
        for key, value in sampling.items():
            if key not in SAMPLING_RANGES:
                raise HTTPException(422, f'Sampling không hỗ trợ trường {key}')
            minimum, maximum = SAMPLING_RANGES[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise HTTPException(422, f'Sampling {key} nằm ngoài khoảng cho phép')
            if key in ('top_k', 'seed') and type(value) is not int:
                raise HTTPException(422, f'Sampling {key} phải là số nguyên')
        settings['sampling_params'] = sampling
    return settings
