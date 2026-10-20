"""Small, local feedback dataset. No model calls, credentials or conversation history."""
import hashlib
import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

MAX_SAMPLES = 5000
REVIEW_TARGET = 50
LABELS = {'hassil': '本地', 'llm': '云端', '': ''}
STATUS_NAMES = {'pending': '待空闲分析', 'researching': '正在分析', 'learned': '已补充本地句式',
                'review_only': '仅保留分析', 'error': '分析未完成', 'not_queued': '仅收集待标注'}


def normalize(text):
    # Keep numbers, negation, conditions and wording. Never guess semantic equivalence.
    text = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()
    text = re.sub(r'(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])', '', text)
    return text.rstrip(' 。.!！?？,，;；、').strip()


def sample_id(owner, text):
    return hashlib.sha256((owner + '|' + normalize(text)).encode()).hexdigest()[:24]


def collect(data, owner, text, now, *, status='pending', reason='', failure=None, contextual=False, count=1):
    key = sample_id(owner, text)
    rows = data.setdefault('samples', [])
    row = next((r for r in rows if r['id'] == key and r['owner'] == owner), None)
    if row:
        row['count'] += count
        row['last_seen'] = max(now, row['last_seen'])
        row['first_seen'] = min(now, row['first_seen'])
        return row, False
    if len(rows) >= MAX_SAMPLES:
        full = data.setdefault('sample_overflow', {})
        full[owner] = full.get(owner, 0) + count
        return None, False
    row = {'id': key, 'owner': owner, 'text': text, 'count': count, 'first_seen': now, 'last_seen': now,
           'predicted_route': 'hassil', 'final_route': 'llm', 'label': '', 'label_source': '',
           'analysis_status': status, 'reason': reason[:1200], 'contextual': bool(contextual),
           'failure': failure or []}
    rows.append(row)
    return row, True


def migrate(data, now):
    """Merge legacy research duplicates once; seed samples without recounting at every boot."""
    if data.get('samples_version') == 1:
        return False
    grouped = {}
    rank = {'learned': 5, 'review_only': 4, 'error': 3, 'researching': 2, 'pending': 1}
    for case in data['cases']:
        key = sample_id(case['owner'], case['text'])
        case = dict(case, count=case.get('count', 1), last_seen=case.get('last_seen', case.get('created_at', now)))
        old = grouped.get(key)
        if old:
            winner = case if rank.get(case['status'], 0) > rank.get(old['status'], 0) else old
            winner = dict(winner, count=old['count'] + case['count'],
                          created_at=min(old.get('created_at', now), case.get('created_at', now)),
                          last_seen=max(old['last_seen'], case['last_seen']))
            grouped[key] = winner
        else:
            grouped[key] = case
    data['cases'] = list(grouped.values())
    for case in data['cases']:
        row, _ = collect(data, case['owner'], case['text'], case['last_seen'],
                         status=case['status'], reason=case.get('reason', ''), failure=case.get('failure'), count=case['count'])
        if row:
            row['first_seen'] = case.get('created_at', now)
    data['samples_version'] = 1
    return True


def sync_analysis(data, case):
    key = sample_id(case['owner'], case['text'])
    row = next((r for r in data.get('samples', []) if r['id'] == key and r['owner'] == case['owner']), None)
    if row:
        row.update(analysis_status=case['status'], reason=case.get('reason', '')[:1200])


def summary(data, owner):
    rows = [r for r in data.get('samples', []) if r['owner'] == owner]
    reviewed = sum(r.get('label') in ('hassil', 'llm') for r in rows)
    counts = {label: sum(r.get('label') == label for r in rows) for label in ('hassil', 'llm')}
    total = sum(r['count'] for r in rows)
    return {'unique': len(rows), 'occurrences': total, 'duplicates': total - len(rows),
            'reviewed': reviewed, 'unreviewed': len(rows) - reviewed, 'labels': counts,
            'review_target': REVIEW_TARGET, 'ready_for_review': len(rows) >= REVIEW_TARGET,
            'ready_for_training_review': reviewed >= REVIEW_TARGET,
            'capacity': MAX_SAMPLES, 'overflow': data.get('sample_overflow', {}).get(owner, 0)}


def sample_page(data, owner, offset=0, limit=30):
    rows = sorted((r for r in data.get('samples', []) if r['owner'] == owner),
                  key=lambda r: r['last_seen'], reverse=True)
    return [{k: v for k, v in r.items() if k != 'owner'} for r in rows[offset:offset + limit]]


def set_label(data, owner, key, label, now):
    if label not in LABELS:
        raise ValueError('标签只能是 hassil、llm 或留空。')
    row = next((r for r in data.get('samples', []) if r['id'] == key and r['owner'] == owner), None)
    if not row:
        raise ValueError('没有找到当前账号的样本。')
    row.update(label=label, label_source='manual' if label else '', reviewed_at=now)


def _xml_text(value):
    # XLSX inline strings cannot become formulas, even if the utterance starts with = or +.
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(value))[:32767]
    return escape(value)


def export_xlsx(data, owner, reviewed_only=False):
    """Dependency-free runtime export; one row per unique instruction, label penultimate."""
    rows = sorted((r for r in data.get('samples', []) if r['owner'] == owner
                   and (not reviewed_only or r.get('label') in ('hassil', 'llm'))), key=lambda r: r['first_seen'])
    if not rows:
        raise ValueError('还没有已确认的样本。' if reviewed_only else '还没有可导出的样本。')
    headers = ['ID', '测试指令', '出现次数', '首次出现', '最近出现', '预测分流', '最终处理',
               '分析状态', '分析原因', '本地失败', '推荐路由', '备注']
    values = [headers]
    import json
    for r in rows:
        note = '人工已确认' if r.get('label') else '待人工确认：最终交给 LLM 不等于正确标签为云端'
        if r.get('contextual'):
            note += '；依赖上下文，不宜单独作为训练句'
        values.append([r['id'], r['text'], r['count'], r['first_seen'], r['last_seen'],
                       r['predicted_route'], r['final_route'], STATUS_NAMES.get(r['analysis_status'], r['analysis_status']),
                       r.get('reason', ''), json.dumps(r.get('failure', []), ensure_ascii=False),
                       LABELS.get(r.get('label', ''), ''), note])
    xml_rows = []
    for i, cells in enumerate(values, 1):
        xml_cells = []
        for col, value in enumerate(cells):
            address = chr(65 + col) + str(i)
            if i > 1 and col == 2:
                xml_cells.append(f'<c r="{address}"><v>{int(value)}</v></c>')
            else:
                xml_cells.append(f'<c r="{address}" t="inlineStr" s="{1 if i == 1 else 0}"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>')
        xml_rows.append(f'<row r="{i}">{"".join(xml_cells)}</row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    worksheet = (f'<worksheet xmlns="{ns}"><dimension ref="A1:L{len(values)}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols><col min="1" max="1" width="26" customWidth="1"/><col min="2" max="2" width="44" customWidth="1"/>'
        '<col min="3" max="3" width="12" customWidth="1"/><col min="4" max="5" width="28" customWidth="1"/>'
        '<col min="6" max="8" width="18" customWidth="1"/><col min="9" max="10" width="48" customWidth="1"/>'
        '<col min="11" max="11" width="16" customWidth="1"/><col min="12" max="12" width="60" customWidth="1"/></cols>'
        f'<sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:L{len(values)}"/>'
        f'<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="请选择标签" error="只能填写本地或云端" sqref="K2:K{len(values)}"><formula1>"本地,云端"</formula1></dataValidation></dataValidations></worksheet>')
    styles = (f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF146175"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    files = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="路由训练样本" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': worksheet, 'xl/styles.xml': styles}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content)
    return output.getvalue(), len(rows)
