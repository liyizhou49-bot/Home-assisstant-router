import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('lab_pipeline',
    Path(__file__).parent/'custom_components/router_lab/pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def callbacks(self, route='hassil', local_results=None, correction=None, with_llm=False):
        self.calls = []
        local_results = iter(local_results or [('done', True, False)])

        async def classify(text):
            return {'route': route, 'scores': {'hassil': .8, 'llm': .2}}

        async def local(text):
            self.calls.append(('local', text))
            return next(local_results)

        async def correct(text):
            self.calls.append(('correct', text))
            return correction or {'accepted': False}

        async def llm(text):
            self.calls.append(('llm', text))
            return 'llm_result', True, False

        return classify, local, correct, llm if with_llm else None

    async def test_llm_route_does_not_touch_local_devices(self):
        result, trace = await pipeline.dispatch('聊天', *self.callbacks(route='llm'))
        self.assertEqual(trace['status'], 'needs_llm')
        self.assertEqual(self.calls, [])
        self.assertIsNone(result)

    async def test_successful_local_command_not_repeated(self):
        result, trace = await pipeline.dispatch('打开客厅灯', *self.callbacks())
        self.assertEqual(result, 'done')
        self.assertEqual(self.calls, [('local', '打开客厅灯')])

    async def test_execution_failure_never_retried_or_sent_to_llm(self):
        result, trace = await pipeline.dispatch('动作', *self.callbacks(
            local_results=[('partially_failed', False, False)], with_llm=True))
        self.assertEqual(trace['status'], 'local_error_no_retry')
        self.assertEqual(self.calls, [('local', '动作')])

    async def test_corrected_command_must_pass_local_handler(self):
        result, trace = await pipeline.dispatch('关必客厅灯', *self.callbacks(
            local_results=[('no_match', False, True), ('done', True, False)],
            correction={'accepted': True, 'canonical_text': '关闭客厅灯'}))
        self.assertEqual(trace['status'], 'corrected_local_success')
        self.assertEqual(self.calls[-1], ('local', '关闭客厅灯'))

    async def test_failed_correction_falls_back_with_original_text(self):
        result, trace = await pipeline.dispatch('原始错字', *self.callbacks(
            local_results=[('no_match', False, True), ('no_match', False, True)],
            correction={'accepted': True, 'canonical_text': '候选改写'}, with_llm=True))
        self.assertEqual(self.calls[-1], ('llm', '原始错字'))

    async def test_router_failure_cannot_execute_devices(self):
        callbacks = self.callbacks(with_llm=True)

        async def broken(_text):
            raise OSError('offline')

        result, trace = await pipeline.dispatch('打开灯', broken, *callbacks[1:])
        self.assertEqual(trace['status'], 'router_unavailable')
        self.assertEqual(self.calls, [])

    async def test_malformed_scores_rejected_without_actions(self):
        callbacks = self.callbacks()

        async def malformed(_text):
            return {'route':'hassil', 'scores':{'hassil':float('nan'), 'llm':.2}}

        result, trace = await pipeline.dispatch('打开灯', malformed, *callbacks[1:])
        self.assertEqual(self.calls, [])
        self.assertEqual(trace['status'], 'router_unavailable')


if __name__ == '__main__':
    unittest.main()
