"""Bounded exact-byte reads consistent with distinct-version lineage checks."""
from hashlib import sha256
import re

from .contracts import literal


def load(query, cik, accession, document_name, expected_hash, *, maximum_bytes=16*1024**2, guard=None):
    if (not isinstance(cik, str) or not re.fullmatch('0*[1-9][0-9]{0,9}', cik)
            or not isinstance(accession,str) or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}', accession)
            or not isinstance(document_name, str) or not 1 <= len(document_name) <= 256
            or not isinstance(expected_hash,str) or not re.fullmatch('[0-9a-f]{64}', expected_hash)
            or type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 64*1024**2):
        raise ValueError('Invalid bounded original document request')
    ciks=','.join(map(literal, sorted({str(int(cik)), str(int(cik)).zfill(10)})))
    where=(f'cik IN ({ciks}) AND accession_number={literal(accession)} '
           f'AND document_name={literal(document_name)}')
    # Do not filter by the expected hash: that would conceal competing versions.
    if guard:guard()
    census=query('SELECT DISTINCT content_sha256,length(source_text) bytes '
                 f'FROM q_live.sec_filing_text_v3 PREWHERE {where} LIMIT 2')
    if len(census)!=1 or census[0]['content_sha256']!=expected_hash:
        raise ValueError('Original payload metadata is missing, ambiguous or changed')
    size=int(census[0]['bytes'])
    if not 0 < size <= maximum_bytes:
        raise ValueError(f'Original payload exceeds byte bound: {size} > {maximum_bytes}')
    if guard:guard()
    rows=query('SELECT DISTINCT source_text FROM q_live.sec_filing_text_v3 '
               f'PREWHERE {where} AND length(source_text)<={maximum_bytes} LIMIT 2')
    if len(rows)!=1:
        raise ValueError('Original payload is missing or has competing bytes')
    payload=rows[0]['source_text'];raw=payload.encode('utf-8')
    if len(raw)!=size or sha256(raw).hexdigest()!=expected_hash:
        raise ValueError('Original payload bytes differ from current lineage')
    if guard:guard()
    return payload
