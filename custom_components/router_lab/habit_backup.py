"""Local, versioned habit snapshots. Backing up never mutates habit memory."""
import copy
import hashlib
import json
from pathlib import Path

FORMAT = 'router_lab_habits_backup'


def checksum(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def counts(data):
    profiles = list(data.get('profiles', {}).values())
    return {'profiles': len(profiles), 'rules': sum(len(p.get('rules', [])) for p in profiles),
            'notes': sum(len(p.get('notes', [])) for p in profiles),
            'temporary': sum(len(p.get('temporary', [])) for p in profiles),
            'suggestions': sum(len(p.get('suggestions', [])) for p in profiles)}


def make_payload(data, entry_id, created_at):
    snapshot = copy.deepcopy(data)
    return {'format': FORMAT, 'backup_version': 1, 'created_at': created_at,
            'source_entry_id': entry_id, 'storage_version': 1,
            'sha256': checksum(snapshot), 'data': snapshot}


def validate_payload(payload):
    if (not isinstance(payload, dict) or payload.get('format') != FORMAT
            or payload.get('backup_version') != 1 or payload.get('storage_version') != 1):
        raise ValueError('不支持的习惯备份格式。')
    data = payload.get('data')
    if (not isinstance(data, dict) or not isinstance(data.get('profiles'), dict)
            or not isinstance(data.get('revision'), int)):
        raise ValueError('备份内容不完整。')
    if payload.get('sha256') != checksum(data):
        raise ValueError('备份校验失败，文件可能不完整或已被修改。')
    return copy.deepcopy(data)


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    validate_payload(json.loads(temporary.read_text('utf-8')))
    temporary.replace(path)


def _read(path):
    payload = json.loads(Path(path).read_text('utf-8'))
    validate_payload(payload)
    return payload


async def manage_backup(memory, operation='status', filename=None):
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.util import dt as dt_util
    from homeassistant.util.ulid import ulid_now
    from .const import SIGNAL_TRACE

    index = memory.backup_index
    if operation == 'status':
        return {'last_created_at': index.get('last_created_at'),
                'backup_count': len(index['backups']), 'backups': list(reversed(index['backups'][-50:]))}
    folder = memory.hass.config.path('router_lab_backups', memory.entry.entry_id)
    if operation == 'create':
        async with memory.lock:
            index = memory.backup_index
            created = dt_util.now().isoformat()
            payload = make_payload(memory.data, memory.entry.entry_id, created)
            filename = 'habits_' + dt_util.now().strftime('%Y%m%d_%H%M%S') + '_' + ulid_now()[-8:] + '.json'
            path = str(Path(folder) / filename)
            await memory.hass.async_add_executor_job(_write, path, payload)
            metadata = {'filename': filename, 'created_at': created,
                        'revision': memory.data['revision'], 'counts': counts(payload['data']),
                        'sha256': payload['sha256']}
            updated = {'last_created_at': created, 'backups': [*index['backups'], metadata]}
            await memory.backup_index_store.async_save(updated)
            memory.backup_index = updated
        async_dispatcher_send(memory.hass, SIGNAL_TRACE)
        return {'filename': filename, 'backup': payload, 'counts': metadata['counts'],
                'created_at': created, 'saved_to_ha': True}
    if operation == 'download':
        backups = index['backups']
        if not backups:
            raise ValueError('还没有备份，请先保存一份。')
        metadata = next((b for b in backups if b['filename'] == filename), None) if filename else backups[-1]
        if not metadata:
            raise ValueError('没有找到这份备份记录。')
        # Filenames come from the trusted saved index, never from a user-supplied path.
        if Path(metadata['filename']).name != metadata['filename']:
            raise ValueError('备份记录无效。')
        payload = await memory.hass.async_add_executor_job(_read, str(Path(folder) / metadata['filename']))
        if payload['sha256'] != metadata['sha256']:
            raise ValueError('备份与保存记录不一致。')
        return {'filename': metadata['filename'], 'backup': payload,
                'counts': counts(payload['data']), 'created_at': metadata['created_at'], 'saved_to_ha': True}
    raise ValueError('未知备份操作。')
