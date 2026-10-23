"""Recover an incomplete transfer receipt using its original representation ETag.

Never edit the original ledger or infer a hash from size alone. Recovery is a
new observation of retained bytes, not a claim that a complete download occurred
at the original partial-transfer timestamp.
"""
from datetime import datetime,timezone
from hashlib import sha256,md5
from pathlib import Path
import re
from .contracts import digest

VERSION='sec-retained-multipart-receipt-recovery-v1'
FIELDS=('source_kind','source_url','source_date','downloaded_at_utc','byte_size','sha256','last_modified','status')
PART_BYTES=5*1024**2


def recover(path,original,repair,*,guard=lambda:None):
    path=Path(path);before=path.stat()
    etag=str(original.get('etag','')).strip('"')
    if (original.get('status')!='downloaded' or original.get('source_kind')!='daily_feed_archive'
            or not re.fullmatch(r'[0-9a-f]{32}-[1-9][0-9]*',etag)
            or type(original.get('byte_size')) is not int or not 0<original['byte_size']<before.st_size
            or not re.fullmatch('[0-9a-f]{64}',original.get('sha256',''))
            or repair.get('repair_status')!='redownloaded_ok' or repair.get('status')!='ok'
            or repair.get('archive_date')!=original.get('source_date')
            or repair.get('archive_bytes')!=before.st_size or repair.get('truncated_by_limit') is not False
            or repair.get('parse_errors')!=0):
        raise ValueError('Recovery requires original ETag and complete successful repair evidence')
    if (before.st_size+PART_BYTES-1)//PART_BYTES!=int(etag.split('-')[1]):
        raise ValueError('Unsupported multipart geometry; no ETag guessing')
    whole=sha256();prefix=sha256();multipart=md5();remaining=original['byte_size'];parts=0
    guard()
    with path.open('rb') as stream:
        while chunk:=stream.read(PART_BYTES):
            guard();whole.update(chunk);multipart.update(md5(chunk).digest());parts+=1
            prefix.update(chunk[:remaining]);remaining=max(0,remaining-len(chunk))
    after=path.stat();guard()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Retained archive changed during recovery')
    calculated=multipart.hexdigest()+'-'+str(parts)
    if prefix.hexdigest()!=original['sha256'] or calculated!=etag:
        raise ValueError('Retained prefix or complete representation ETag differs')
    now=datetime.now(timezone.utc).isoformat()
    proof=dict(version=VERSION,original_receipt=dict(original),repair_evidence=dict(repair),
        part_bytes=PART_BYTES,parts=parts,calculated_etag=calculated,prefix_sha256=prefix.hexdigest(),
        full_sha256=whole.hexdigest(),full_bytes=before.st_size,verified_at_utc=now)
    proof['proof_hash']=digest(proof)
    result={k:original[k] for k in FIELDS}
    result.update(status='recovered_verified_representation',sha256=whole.hexdigest(),byte_size=before.st_size,
        downloaded_at_utc=now,recovery=proof)
    validate(result)
    return result


def validate(receipt):
    proof=receipt.get('recovery',{});body=dict(proof);sealed=body.pop('proof_hash',None)
    original=proof.get('original_receipt',{});repair=proof.get('repair_evidence',{})
    if (sealed!=digest(body) or proof.get('version')!=VERSION or proof.get('part_bytes')!=PART_BYTES
            or receipt.get('status')!='recovered_verified_representation'
            or original.get('status')!='downloaded' or original.get('source_kind')!='daily_feed_archive'
            or any(receipt.get(k)!=original.get(k) for k in ('source_kind','source_url','source_date','last_modified'))
            or proof.get('prefix_sha256')!=original.get('sha256')
            or proof.get('calculated_etag')!=str(original.get('etag','')).strip('"')
            or not re.fullmatch(r'[0-9a-f]{32}-[1-9][0-9]*',proof.get('calculated_etag',''))
            or proof.get('full_sha256')!=receipt.get('sha256')
            or not re.fullmatch('[0-9a-f]{64}',receipt.get('sha256',''))
            or proof.get('full_bytes')!=receipt.get('byte_size')
            or proof.get('verified_at_utc')!=receipt.get('downloaded_at_utc')
            or repair.get('status')!='ok' or repair.get('repair_status')!='redownloaded_ok'
            or repair.get('archive_bytes')!=receipt.get('byte_size')
            or repair.get('archive_date')!=receipt.get('source_date')
            or repair.get('parse_errors')!=0 or repair.get('truncated_by_limit') is not False):
        raise ValueError('Recovered SEC receipt evidence differs')
    if (type(receipt['byte_size']) is not int or type(original.get('byte_size')) is not int
            or not 0<original['byte_size']<receipt['byte_size']
            or proof.get('parts')!=(receipt['byte_size']+PART_BYTES-1)//PART_BYTES
            or int(proof['calculated_etag'].split('-')[1])!=proof['parts']):
        raise ValueError('Recovered SEC receipt geometry differs')
    start=datetime.fromisoformat(original['downloaded_at_utc'].replace('Z','+00:00'))
    end=datetime.fromisoformat(receipt['downloaded_at_utc'].replace('Z','+00:00'))
    if start.tzinfo is None or end.tzinfo is None or end<start:
        raise ValueError('Recovered receipt chronology differs')


def canonical(receipt):
    result={k:receipt[k] for k in FIELDS}
    if receipt.get('status')=='recovered_verified_representation':
        validate(receipt);result['recovery']=receipt['recovery']
    elif receipt.get('status')!='downloaded':
        raise ValueError('Unknown SEC download receipt status')
    return result


def select_complete(candidates, size):
    """Choose earliest download of identical bytes; conflicting evidence blocks."""
    rows=[canonical(r) for r in candidates if r.get('byte_size')==size]
    if not rows:return None
    identity=lambda r:tuple(r[k] for k in FIELDS if k!='downloaded_at_utc')
    if len({identity(r) for r in rows})!=1:
        raise ValueError('Competing complete SEC archive receipts')
    def timestamp(r):
        value=datetime.fromisoformat(r['downloaded_at_utc'].replace('Z','+00:00'))
        if value.tzinfo is None:raise ValueError('Archive receipt timestamp is not aware')
        return value
    return min(rows,key=timestamp)
