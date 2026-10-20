"""Feedback lifecycle, human labels and runtime XLSX interoperability; no live API calls."""
import asyncio
import copy
import io
import json
import sys
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import test_route_learning as fixtures

samples = __import__(fixtures.PACKAGE + '.route_samples', fromlist=['x'])
NOW = '2026-10-20T12:00:00+08:00'


def trace(text='客厅灯亮度拧到30%', **fields):
    return dict(input=text, predicted_route='hassil', final_route='llm',
                attempts=[{'handler': 'hassil'}], local_failures=[{'code': 'no_intent_match'}], **fields)


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    make = fixtures.IdleTests.make

    async def test_punctuation_fullwidth_and_spacing_duplicates_survive_reload_without_reanalysis(self):
        learning, _ = self.make()
        await learning.enqueue('alice', trace(), None)
        learning.data['cases'][0]['status'] = 'review_only'
        learning.data['cases'][0]['reason'] = '保留分析'
        samples.sync_analysis(learning.data, learning.data['cases'][0])
        await learning.save()
        await learning.load()
        await learning.enqueue('alice', trace('  客厅灯亮度拧到３０％。  '), None)
        self.assertEqual(len(learning.data['cases']), 1)
        self.assertEqual(learning.data['cases'][0]['status'], 'review_only')
        self.assertEqual(learning.data['samples'][0]['count'], 2)
        self.assertEqual(learning.data['cases'][0]['count'], 2)
        calls = []
        async def reply(*args): calls.append(1); return fixtures.proposal()
        learning._json_call = reply
        await learning._work()
        self.assertEqual(calls, [])

    async def test_numbers_negation_and_different_owners_are_not_merged(self):
        learning, _ = self.make()
        for owner, text in [('alice', '设置空调26度'), ('alice', '设置空调28度'),
                            ('alice', '不要设置空调26度'), ('bob', '设置空调26度')]:
            await learning.enqueue(owner, trace(text), None)
        self.assertEqual(len(learning.data['samples']), 4)
        self.assertEqual(len(learning.view('alice')['samples']), 3)
        self.assertEqual(len(learning.view('bob')['samples']), 1)
        self.assertTrue(all('owner' not in r for r in learning.view('alice')['samples']))

    async def test_subjective_or_contextual_failures_are_collected_without_extension_research(self):
        learning, _ = self.make()
        await learning.enqueue('alice', trace('当门锁打开，就关闭全屋灯光'), None)
        await learning.enqueue('alice', trace('再调高一点', contextual_override=True), None)
        self.assertEqual(len(learning.data['samples']), 2)
        self.assertEqual(learning.data['cases'], [])
        self.assertTrue(all(r['analysis_status'] == 'not_queued' for r in learning.data['samples']))
        await learning.enqueue('alice', dict(trace(), predicted_route='llm'), None)
        self.assertEqual(len(learning.data['samples']), 2)

    async def test_migration_merges_legacy_finished_and_pending_duplicates_exactly_once(self):
        learning, _ = self.make()
        first = dict(fixtures.case(), created_at=NOW, status='review_only')
        second = dict(fixtures.case(), id='second', text=first['text'] + '。', created_at=NOW)
        learning.store.saved = {'rules': [], 'cases': [first, second], 'usage': {}, 'revision': 0}
        await learning.load()
        self.assertEqual(len(learning.data['cases']), 1)
        self.assertEqual(learning.data['cases'][0]['status'], 'review_only')
        self.assertEqual(learning.data['samples'][0]['count'], 2)
        await learning.load()
        self.assertEqual(learning.data['samples'][0]['count'], 2)

    async def test_manual_label_scoped_and_not_inferred_from_llm_success(self):
        learning, _ = self.make()
        await learning.enqueue('alice', trace(), None)
        row = learning.data['samples'][0]
        self.assertEqual(row['label'], '')
        with self.assertRaises(ValueError):
            await learning.manage('bob', 'label', sample_id=row['id'], label='llm')
        await learning.manage('alice', 'label', sample_id=row['id'], label='llm')
        self.assertEqual(learning.data['cases'][0]['status'], 'review_only')
        self.assertEqual(learning.view('alice')['sample_summary']['labels']['llm'], 1)
        await learning.enqueue('alice', trace(), None)
        self.assertEqual(row['label'], 'llm')
        await learning.manage('alice', 'retry')
        self.assertEqual(learning.data['cases'][0]['status'], 'review_only')

    async def test_cancel_research_on_llm_label_does_not_install_proposed_rule(self):
        learning, calls = self.make()
        await learning.enqueue('alice', trace(), None)
        started = asyncio.Event()
        async def wait(*args): started.set(); await asyncio.Future()
        learning._json_call = wait
        learning.task = asyncio.create_task(learning._work()); await started.wait()
        await learning.manage('alice', 'label', sample_id=learning.data['samples'][0]['id'], label='llm')
        self.assertEqual(learning.data['cases'][0]['status'], 'review_only')
        self.assertEqual(learning.data['rules'], [])
        self.assertEqual(calls, [])

    async def test_new_dataset_cap_never_evicts_existing_and_duplicates_still_count(self):
        learning, _ = self.make()
        with patch.object(samples, 'MAX_SAMPLES', 1):
            await learning.enqueue('alice', trace(), None)
            await learning.enqueue('alice', trace('打开客厅电视'), None)
            await learning.enqueue('alice', trace(), None)
        self.assertEqual(len(learning.data['samples']), 1)
        self.assertEqual(learning.data['samples'][0]['count'], 2)
        self.assertEqual(learning.view('alice')['sample_summary']['overflow'], 1)

    async def test_archive_does_not_forget_after_one_hundred_different_phrases(self):
        learning, _ = self.make()
        for n in range(105):
            await learning.enqueue('alice', trace(f'客厅灯亮度拧到{n}%'), None)
        await learning.enqueue('alice', trace('客厅灯亮度拧到0%。'), None)
        self.assertEqual(len(learning.data['samples']), 105)
        self.assertEqual(len(learning.data['cases']), 105)
        self.assertEqual(learning.data['cases'][0]['count'], 2)
        self.assertEqual(len(learning.view('alice')['samples']), 30)
        self.assertEqual(len(learning.view('alice', 90)['samples']), 15)

    async def test_exports_only_current_owner_and_confirmed_rows_when_selected(self):
        learning, _ = self.make()
        learning.hass.async_add_executor_job = lambda f, *a: asyncio.to_thread(f, *a)
        await learning.enqueue('alice', trace(), None)
        await learning.enqueue('alice', trace('打开客厅电视'), None)
        await learning.enqueue('bob', trace('私有设备名称'), None)
        await learning.manage('alice', 'label', sample_id=learning.data['samples'][0]['id'], label='hassil')
        result = await learning.manage('alice', 'export', reviewed_only=True)
        self.assertEqual(result['rows'], 1)
        self.assertTrue(result['filename'].endswith('.xlsx'))
        self.assertEqual(learning.view('alice')['sample_summary']['reviewed'], 1)


class WorkbookTests(unittest.TestCase):
    def test_xlsx_can_be_read_by_original_training_importer_and_strings_are_not_formulas(self):
        import openpyxl
        data = {'samples': []}
        r, _ = samples.collect(data, 'alice', '=1+2\x00 & <空调>', NOW)
        samples.set_label(data, 'alice', r['id'], 'llm', NOW)
        samples.collect(data, 'bob', '另一账号的设备', NOW)
        content, count = samples.export_xlsx(data, 'alice', True)
        self.assertEqual(count, 1)
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        try:
            rows = list(wb.active.values)
            self.assertEqual(rows[0][-2], '推荐路由')
            self.assertIn('测试指令', rows[0])
            self.assertEqual(rows[1][-2], '云端')
            self.assertEqual(rows[1][1], '=1+2 & <空调>')
            self.assertEqual(wb.active['B2'].data_type, 's')
            self.assertEqual(rows[1][2], 1)
        finally: wb.close()
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            xml = z.read('xl/worksheets/sheet1.xml').decode()
            self.assertNotIn('<f>', xml)
            self.assertNotIn('另一账号', xml)
        import tempfile
        training_source = fixtures.ROOT.parent / 'HA 路由'
        if (training_source / 'ha_router/data.py').exists():
            sys.path.insert(0, str(training_source))
            from ha_router.data import read_rows
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'reviewed.xlsx'; path.write_bytes(content)
                imported = read_rows(path)
                self.assertEqual(imported[0]['label'], 'llm')
                self.assertEqual(imported[0]['text'], '=1+2 & <空调>')

    def test_unreviewed_export_does_not_label_all_fallbacks_llm(self):
        import openpyxl
        data = {'samples': []}; samples.collect(data, 'alice', '灯有点暗', NOW)
        with self.assertRaises(ValueError): samples.export_xlsx(data, 'alice', True)
        content, _ = samples.export_xlsx(data, 'alice')
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        try:
            values = list(wb.active.values)
            self.assertIn(values[1][-2], ('', None))
            self.assertIn('待人工确认', values[1][-1])
        finally: wb.close()

    def test_threshold_counts_unique_reviewed_samples_not_repeated_utterances(self):
        data = {'samples': []}
        for _ in range(80): samples.collect(data, 'alice', '客厅灯亮度30%', NOW)
        report = samples.summary(data, 'alice')
        self.assertEqual(report['unique'], 1)
        self.assertEqual(report['duplicates'], 79)
        self.assertFalse(report['ready_for_training_review'])
        self.assertEqual(report['reviewed'], 0)


if __name__ == '__main__': unittest.main()
