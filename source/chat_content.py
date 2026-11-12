"""Parse model-generated planning option JSON without mistaking arbitrary JSON examples for cards.

The fallback is deliberately conservative: user approval is never inferred from an AI answer.
"""
import json
import re
from planning import normalize_question

_DECODER = json.JSONDecoder()
_WHOLE_JSON_FENCE = re.compile(r'\s*```(?:json)?[ \t]*\r?\n\s*(\{[\s\S]+?\})\s*```\s*', re.IGNORECASE)

EMPTY_REPLY = '(Không có nội dung phản hồi)'
OUTPUT_LIMIT_PHASES = {'output_limit', 'document_output_limit'}
_TOOL_MARKUP = re.compile(r'(?m)^[ \t]*<tool_call>[ \t]*\r?\n[ \t]*<function(?=[=\s>]|$)')


def split_incomplete_tool_call(text):
    """Remove a trailing tool-call fragment emitted as plain model text."""
    for match in _TOOL_MARKUP.finditer(text):
        prefix = text[:match.start()]
        if prefix.count('```') % 2 == 0 and prefix.count('~~~') % 2 == 0 and '</tool_call>' not in text[match.start():]:
            return prefix.rstrip(), True
    return text, False


def turn_notice(text, events, *, limited=None, pending=False, handoff=False,
                incomplete_tool=False, tool_finished=False):
    """Describe a turn's result without manufacturing an assistant reply."""
    for event in reversed(events):
        if event.get('type') == 'outcome':
            return event['notice']
    if limited is None:
        limited = any(event.get('phase') in OUTPUT_LIMIT_PHASES or
                      event.get('message') == 'Lệnh ghi tài liệu bị cắt do giới hạn đầu ra.'
                      for event in events)
    text, incomplete_tail = split_incomplete_tool_call(text)
    incomplete_tool = incomplete_tool or incomplete_tail
    if text.strip() not in ('', EMPTY_REPLY) and not (limited or incomplete_tool):
        return None
    tool_finished = tool_finished or any(
        event.get('type') == 'tool' and event.get('status') in ('complete', 'error')
        for event in events
    )
    if handoff:
        return {'label':'Bản nháp sẵn sàng', 'message':'SpecWeave đã bàn giao bản nháp để bạn duyệt.'}
    if limited:
        return {'label':'Đầu ra bị cắt',
                'message':'Model chạm giới hạn đầu ra trước khi kết thúc. Xem hoạt động của lượt chạy để kiểm tra phần đã xử lý.'}
    if incomplete_tool:
        return {'label':'Lệnh công cụ chưa hoàn chỉnh',
                'message':'Model gửi một phần lệnh gọi công cụ ở dạng văn bản. SpecWeave không thể thực thi phần lệnh đó.'}
    if pending:
        return {'label':'Chờ xác nhận', 'message':'Chọn một phương án trong câu hỏi đang chờ để tiếp tục.'}
    if tool_finished:
        return {'label':'Có hoạt động công cụ',
                'message':'SpecWeave đã chạy công cụ trong lượt này và chưa gửi thêm câu trả lời.'}
    return {'label':'Chưa có câu trả lời',
            'message':'Lượt SpecWeave đã kết thúc mà chưa có câu trả lời bằng văn bản. Bạn có thể yêu cầu SpecWeave tiếp tục.'}


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
