"""Normalize ONLY provider-reported usage; never invent token counts or prices."""

def _non_negative(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(raw, *, source):
    if not isinstance(raw, dict): return {}
    input_count = _non_negative(raw.get('prompt_tokens'))
    if input_count is None: input_count = _non_negative(raw.get('inputTokens'))
    if input_count is None: input_count = _non_negative(raw.get('input'))
    output_count = _non_negative(raw.get('completion_tokens'))
    if output_count is None: output_count = _non_negative(raw.get('outputTokens'))
    if output_count is None: output_count = _non_negative(raw.get('output'))
    details = raw.get('prompt_tokens_details') or {}
    if not isinstance(details, dict): details={}
    cache_read = _non_negative(details.get('cached_tokens'))
    if cache_read is None: cache_read = _non_negative(raw.get('cacheRead'))
    cache_write = _non_negative(raw.get('cacheWrite'))
    result = {'source':source}
    if input_count is not None: result['input_tokens'] = input_count
    if output_count is not None: result['output_tokens'] = output_count
    if cache_read is not None: result['cache_read_tokens'] = cache_read
    if cache_write is not None: result['cache_write_tokens'] = cache_write
    cost = raw.get('cost')
    if isinstance(cost,dict): cost = cost.get('total')
    if isinstance(cost,(int,float)) and not isinstance(cost,bool) and 0 <= cost < 1000000:
        result['cost_usd'] = round(float(cost),8)
    if len(result)==1:return {}
    return result


def accumulate_usage(total, item):
    if not item: return dict(total)
    result = dict(total)
    result['source'] = item.get('source', result.get('source','provider'))
    for key in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','cost_usd'):
        if key in item:
            result[key] = result.get(key,0) + item[key]
    return result

async def with_heartbeat(events, interval_seconds=12.0):
    """Keep reverse proxies (including tunnels) alive during long model prefill/tool calls.

    A pending __anext__ is *not* cancelled on timeout: that would abort the
    model stream. Only cancel on actual consumer shutdown.
    """
    import asyncio
    iterator=events.__aiter__()
    pending=asyncio.create_task(anext(iterator))
    try:
        while True:
            ready,_=await asyncio.wait({pending},timeout=interval_seconds)
            if not ready:
                yield 'status', {'phase':'waiting','message':'Model vẫn đang xử lý…'}
                continue
            try:
                item=pending.result()
            except StopAsyncIteration:
                break
            yield item
            pending=asyncio.create_task(anext(iterator))
    finally:
        if pending and not pending.done():
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
