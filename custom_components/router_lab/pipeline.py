"""Routing orchestration independent of Home Assistant; never trains a model."""
import math
import time


async def dispatch(text, classify, local, correct, llm=None, *, contextual_llm=False):
    start = time.perf_counter()
    trace = {'input': text, 'predicted_route': None, 'final_route': None,
             'scores': {}, 'correction': None, 'attempts': []}

    def finish(result, status, route, message=None):
        trace.update(status=status, final_route=route,
                     elapsed_ms=round((time.perf_counter()-start)*1000, 2))
        if message:
            trace['message'] = message
        return result, trace

    async def fallback():
        if llm is None:
            return finish(None, 'needs_llm', 'llm',
                          '这句话需要LLM处理，但尚未配置LLM。没有继续执行设备操作。')
        trace['attempts'].append({'handler': 'llm', 'text': text})
        result, _success, _retry = await llm(text)
        return finish(result, 'llm_returned', 'llm')

    try:
        prediction = await classify(text)
        route, scores = prediction['route'], prediction['scores']
        if route not in ('hassil', 'llm') or set(scores) != {'hassil', 'llm'}:
            raise ValueError('invalid route result')
        if not all(isinstance(value, (int, float)) and math.isfinite(value)
                   and 0 <= value <= 1 for value in scores.values()):
            raise ValueError('invalid scores')
        trace.update(predicted_route=route, scores=scores, model=prediction.get('model'))
    except Exception:
        return finish(None, 'router_unavailable', 'error', '路由服务不可用，本次没有执行设备操作。')

    if contextual_llm:
        trace['contextual_override'] = '需要对话上下文或习惯记忆'
    if route == 'llm' or contextual_llm:
        return await fallback()
    trace['attempts'].append({'handler': 'hassil', 'text': text})
    result, success, retryable = await local(text)
    if success:
        return finish(result, 'local_success', 'hassil')
    if not retryable:
        return finish(result, 'local_error_no_retry', 'hassil')

    try:
        correction = await correct(text)
    except Exception:
        correction = {'accepted': False, 'reason': 'correction_unavailable'}
    trace['correction'] = correction
    canonical = correction.get('canonical_text')
    if correction.get('accepted') and isinstance(canonical, str) and canonical != text:
        trace['attempts'].append({'handler': 'hassil_corrected', 'text': canonical})
        result, success, retryable = await local(canonical)
        if success:
            return finish(result, 'corrected_local_success', 'hassil')
        if not retryable:
            return finish(result, 'local_error_no_retry', 'hassil')
    # Always preserve the original ASR text when falling back to the LLM.
    return await fallback()
