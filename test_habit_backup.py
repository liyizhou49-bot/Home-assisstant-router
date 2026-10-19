import asyncio
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

SOURCE=Path(__file__).parent/'custom_components/router_lab'
package=types.ModuleType('backup_test');package.__path__=[str(SOURCE)];sys.modules['backup_test']=package
spec=importlib.util.spec_from_file_location('backup_test.habit_backup',SOURCE/'habit_backup.py')
backup=importlib.util.module_from_spec(spec);sys.modules[spec.name]=backup;spec.loader.exec_module(backup)
for name in ['homeassistant','homeassistant.helpers','homeassistant.helpers.dispatcher',
             'homeassistant.util','homeassistant.util.ulid']:
    sys.modules[name]=types.ModuleType(name)
sys.modules['homeassistant.helpers.dispatcher'].async_dispatcher_send=lambda *args:None
sys.modules['homeassistant.util'].dt=types.SimpleNamespace(now=lambda:datetime.now(timezone.utc))
counter=iter(range(100))
sys.modules['homeassistant.util.ulid'].ulid_now=lambda:f'{next(counter):026d}'


def data():
    return {'revision':7,'profiles':{'user-a':{'rules':[{'id':'rule-1','entity_id':'light.test','value':25}],
              'notes':[{'id':'n1','text':'中文偏好示例'}],'temporary':[], 'suggestions':[],
              'observations':{'correction':{'count':2}}}}}


class BackupTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_is_independent_and_keeps_all_fields(self):
        original=data();saved=backup.make_payload(original,'entry','2026-10-18T12:00:00+08:00')
        self.assertEqual(backup.validate_payload(saved),original)
        original['profiles']['user-a']['rules'][0]['value']=80
        self.assertEqual(saved['data']['profiles']['user-a']['rules'][0]['value'],25)
        self.assertIn('observations',saved['data']['profiles']['user-a'])

    def test_modified_or_unsupported_backup_is_rejected(self):
        saved=backup.make_payload(data(),'entry','now')
        altered=copy.deepcopy(saved);altered['data']['revision']=8
        with self.assertRaises(ValueError):backup.validate_payload(altered)
        saved['backup_version']=999
        with self.assertRaises(ValueError):backup.validate_payload(saved)

    async def test_concurrent_saves_keep_both_files_without_changing_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            class Store:
                async def async_save(self,index):
                    await asyncio.sleep(.01)
                    self.data=copy.deepcopy(index)
            class Hass:
                config=types.SimpleNamespace(path=lambda *parts:str(Path(folder).joinpath(*parts)))
                async def async_add_executor_job(self,fn,*args):return await asyncio.to_thread(fn,*args)
            memory=types.SimpleNamespace(hass=Hass(),entry=types.SimpleNamespace(entry_id='entry'),
                data=data(),lock=asyncio.Lock(),backup_index={'backups':[]},backup_index_store=Store())
            before=copy.deepcopy(memory.data)
            one,two=await asyncio.gather(backup.manage_backup(memory,'create'),backup.manage_backup(memory,'create'))
            self.assertNotEqual(one['filename'],two['filename'])
            self.assertEqual(len(memory.backup_index['backups']),2)
            self.assertEqual(memory.data,before)
            downloaded=await backup.manage_backup(memory,'download',one['filename'])
            self.assertEqual(downloaded['backup']['data'],before)
            with self.assertRaises(ValueError):await backup.manage_backup(memory,'download','../../outside.json')


if __name__=='__main__':unittest.main()
