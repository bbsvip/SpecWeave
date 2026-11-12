"""Parse model-generated planning option JSON without mistaking arbitrary JSON examples for cards.

The fallback is deliberately conservative: user approval is never inferred from an AI answer.
"""
import json
import re
from planning import normalize_question

_DECODER = json.JSONDecoder()
_WHOLE_JSON_FENCE = re.compile(r'\s*```(?:json)?[ \t]*\r?\n\s*(\{[\s\S]+?\})\s*```\s*', re.IGNORECASE)


def _options(value):
    if isinstance(value, dict):
        options = value.get('options')
    elif isinstance(value, list):
        options = value
    else:
        return None
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return None
    if not all(isinstance(o, dict) and isinstance(o.get('label'), str) and o['label'].strip()
               and (o.get('description') or o.get('pros') or o.get('pros_cons')) for o in options):
        return None
    return options


def _format_options(options):
    result = ['\n\n### Các phương án\n']
    for entry in options:
        label = str(entry['label'])[:150]
        desc = str(entry.get('description') or '')[:1200]
        pros = str(entry.get('pros') or '')[:600]
        cons = str(entry.get('cons') or '')[:600]
        trade = str(entry.get('pros_cons') or '')[:1200]
        result.append(f'**{label}**')
        if desc: result.append(desc)
        if pros: result.append(f'- Ưu điểm: {pros}')
        if cons: result.append(f'- Nhược điểm: {cons}')
        if trade and not (pros or cons): result.append(f'- Đánh đổi: {trade}')
        result.append('')
    return '\n'.join(result)


def parse_model_response(text, *, interactive=False):
    """Return (readable Markdown, optional well-formed interactive brief).

    Only recognize complete JSON objects/arrays containing multiple labeled options.
    No eval, no changes to unrelated code blocks, JSON snippets or free text.
    """
    if not isinstance(text, str) or not text or len(text) > 200_000:
        return text, None
    fence = _WHOLE_JSON_FENCE.fullmatch(text) if interactive else None
    if fence:
        cleaned, brief = parse_model_response(fence.group(1), interactive=True)
        return (cleaned, brief) if brief else (text, None)
    for match in re.finditer(r'[\[{]', text):
        start = match.start()
        try:
            obj, size = _DECODER.raw_decode(text[start:])
        except (json.JSONDecodeError, ValueError):
            continue
        options = _options(obj)
        if not options or size > 30_000:
            continue
        # Do not alter fenced code demonstrations with JSON content.
        before = text[:start]
        if before.count('```') % 2:
            continue
        end = start + size
        if text[end:].lstrip().startswith('```'):
            continue
        brief = None
        if isinstance(obj, dict):
            try:
                normalize_question(obj)
                brief = obj
            except (ValueError, TypeError, KeyError):
                pass
        prefix=text[:start].rstrip()
        if interactive and brief:
            return '\n\n'.join(part for part in (prefix, text[end:].strip()) if part), brief
        if isinstance(obj,dict):
            question=str(obj.get('question') or '').strip()[:600]
            context=str(obj.get('context') or '').strip()[:1200]
            why=str(obj.get('recommendation_reason') or '').strip()[:1200]
            if question: prefix+='\n\n**Câu hỏi cần quyết định:** '+question
            if context: prefix+='\n\n'+context
        readable = (prefix + _format_options(options) + text[end:]).strip()
        if isinstance(obj,dict) and why:
            readable+='\n\n**Lý do đề xuất:** '+why
        return readable, brief
    return text, None


def display_user_message(text):
    """Keep the answer bubble consistent with the live composer after reload."""
    header = '[TRẢ LỜI CÂU HỎI LẬP KẾ HOẠCH]\nCâu hỏi: '
    footer = '\nĐây là câu trả lời xác nhận trong trao đổi, KHÔNG phải quyết định đã được duyệt trong Decision Center.'
    if text.startswith(header) and text.endswith(footer):
        _, separator, answer = text[:-len(footer)].partition('\nPhương án người dùng chọn: ')
        if separator:
            return 'Tôi chọn: ' + answer
    return text
