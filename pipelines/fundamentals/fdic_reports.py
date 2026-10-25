"""Bounded official FDIC report acquisition; no implied financial qualification.

Report bytes are immutable and content-addressed. A download receipt proves
retrieval, not historical public availability or accounting interpretation.
"""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

BASE = 'https://securitiesfilings.fdicconnect.fdic.gov/api/instflng'
CERTIFICATES = {'HIFS': 90211, 'TOWN': 35095, 'OZK': 110, 'PFBC': 33539}
MAX_BYTES = 32 * 1024**2


def inventory(rows, ticker, start, end):
    """Select public financial forms using the portal's displayed filing date."""
    cert = CERTIFICATES[ticker]
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    selected = []
    seen = set()
    for row in rows:
        if row['orgCertNum'] != cert:
            raise ValueError('FDIC certificate mismatch')
        form = row['instFlngForm']
        if not re.fullmatch(r'Form 10-[KQ](?:/A)?', form['instFlngFormNme'].split('~')[0]):
            continue
        filing_date = date.fromisoformat(row['sysLstActDttm'])
        if not start <= filing_date <= end:
            continue
        if form['instFlngFormConfInd'] != 'N' or row.get('instFlngAllFilesPrivate'):
            raise ValueError('Non-public FDIC report')
        for attachment in row['instFlngAtchList']:
            filing_id, attachment_id = int(row['instFlngId']), int(attachment['instFlngAtchId'])
            if int(attachment['instFlngId']) != filing_id or min(filing_id, attachment_id) <= 0:
                raise ValueError('FDIC attachment identity mismatch')
            key = (filing_id, attachment_id)
            if key in seen:
                raise ValueError('Duplicate FDIC attachment')
            seen.add(key)
            size = int(attachment['instFlngAtchSizeQty'])
            if not 0 < size <= MAX_BYTES:
                raise ValueError('FDIC attachment exceeds bounded size')
            selected.append(dict(ticker=ticker, certificate=cert, filing_id=filing_id,
                attachment_id=attachment_id, filing_date=str(filing_date),
                form=form['instFlngFormNme'], issuer_name=row['instFincLglName'],
                filename=attachment['instFlngAtchOrglNme'], bytes=size,
                attachment_created_at=attachment['instFlngAtchCrteDttm'],
                attachment_changed_at=attachment['sysLstActDttm'],
                url=f'{BASE}/{filing_id}/attachment/{attachment_id}'))
    return sorted(selected, key=lambda r: (r['filing_date'], r['filing_id'], r['attachment_id']))


def _write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def acquire(report, root, session):
    """Fetch one official PDF, or hash-verify its exact existing receipt."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    expected_url = f"{BASE}/{int(report['filing_id'])}/attachment/{int(report['attachment_id'])}"
    if report['url'] != expected_url or report['certificate'] != CERTIFICATES[report['ticker']]:
        raise ValueError('Unapproved FDIC acquisition identity')
    receipt_path = root / f"{report['filing_id']}-{report['attachment_id']}.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['report'] != report:
            raise ValueError('FDIC metadata changed; retain old receipt for version review')
        content_hash = receipt['sha256']
        if not re.fullmatch('[a-f0-9]{64}', content_hash):
            raise ValueError('Invalid FDIC receipt hash')
        raw = (root / (content_hash + '.pdf')).read_bytes()
        if len(raw) != report['bytes'] or sha256(raw).hexdigest() != content_hash:
            raise ValueError('FDIC retained bytes mismatch')
        return dict(receipt, reused=True)
    with session.get(expected_url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Unexpected FDIC response')
        chunks, size = [], 0
        for chunk in response.iter_content(128 * 1024):
            size += len(chunk)
            if size > min(MAX_BYTES, report['bytes']):
                raise ValueError('FDIC response exceeds declared size')
            chunks.append(chunk)
    raw = b''.join(chunks)
    if len(raw) != report['bytes'] or not raw.startswith(b'%PDF-'):
        raise ValueError('FDIC PDF format or size mismatch')
    content_hash = sha256(raw).hexdigest()
    target = root / (content_hash + '.pdf')
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('FDIC content-address conflict')
    temporary = root / (uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    receipt = dict(report=report, sha256=content_hash, bytes=len(raw),
        downloaded_at=datetime.now(timezone.utc).isoformat(),
        qualification='download_only_not_financial_publication')
    _write_json(receipt_path, receipt)
    return dict(receipt, reused=False)
