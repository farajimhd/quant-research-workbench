"""Exact original-document/bulk comparison; no rounded or approximate certification."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
import json
import re

from . import sec_public_archive_time
from .contracts import digest,literal

VERSION='sec-original-fundamental-parity-v1'


def reconcile(extraction,bulk,witness,qualification,receipt):
    body=dict(extraction);sealed=body.pop('extraction_hash',None)
    if sealed!=digest(body):raise ValueError('SEC fundamental extraction fingerprint differs')
    if (not isinstance(bulk,(tuple,list)) or len(bulk)>10000
            or len(extraction['observations'])+len(extraction['rejected'])>10000):
        raise ValueError('SEC fundamental reconciliation exceeds its bounded fact census')
    if sec_public_archive_time.qualify(witness,receipt,mode=qualification['mode'])!=qualification:
        raise ValueError('SEC fundamental public-time evidence differs')
    if witness['document_hash']!=extraction['source_text_hash']:
        raise ValueError('SEC original document differs from extracted fundamentals')
    def key(row):
        dimensions=tuple(tuple(pair) for pair in row['dimensions'])
        if len({p[0] for p in dimensions})!=len(dimensions):raise ValueError('Duplicate fundamental dimension axis')
        return (row['taxonomy'],row['tag'],row['unit'],row['period_start'],row['period_end'],tuple(sorted(dimensions)))
    original=defaultdict(list)
    for row in extraction['observations']:
        match=re.fullmatch(r'\{(http://fasb.org/us-gaap|http://xbrl.sec.gov/dei)/[^}]+\}(.+)',row['concept'])
        if match is None:raise ValueError('Unsupported extracted fundamental taxonomy')
        normalized=dict(row,taxonomy='us-gaap' if 'fasb.org' in match[1] else 'dei',tag=match[2])
        original[key(normalized)].append(normalized)
    seen=set();used=set();rows=[]
    for row in bulk:
        identity=row['source_record']
        if not isinstance(identity,str) or not 1<=len(identity)<=512 or identity in seen:
            raise ValueError('Missing or duplicate bulk source identity')
        seen.add(identity);candidates=original.get(key(row),[])
        # Never accept Float64 transport as exact source decimal evidence.
        value=row['value']
        if not isinstance(value,str) or not 1<=len(value)<=4096:raise ValueError('Bulk parity requires bounded preserved decimal text')
        try:
            number=Decimal(value)
            if not number.is_finite():raise ValueError('Nonfinite bulk source value')
        except InvalidOperation as error:raise ValueError('Invalid bulk source decimal') from error
        distinct={Decimal(c['value']) for c in candidates}
        status=('missing_original_context' if not candidates else 'original_value_conflict' if len(distinct)>1
                else 'matched' if number in distinct else 'value_mismatch')
        positions=tuple(c['source_position'] for c in candidates)
        numeric_type=row.get('source_numeric_type','')
        if status=='matched' and not re.fullmatch(r'String|Decimal(?:\([0-9]+, ?[0-9]+\))?',numeric_type):
            status='source_precision_unqualified'
        if status=='matched':used.update(positions)
        rows.append(dict(source_record=identity,status=status,source_value=value,original_values=tuple(c['value'] for c in candidates),
            original_positions=positions,context_hash=digest(key(row))))
    result=dict(version=VERSION,extraction_hash=sealed,witness_hash=witness['witness_hash'],
        qualification_hash=qualification['qualification_hash'],available_at=qualification['available_at'],
        precision=qualification['precision'],comparisons=tuple(rows),
        unmatched_original_positions=tuple(sorted(r['source_position'] for r in extraction['observations'] if r['source_position'] not in used)),
        rejected_original=extraction['rejected'],all_bulk_matched=bool(rows) and all(r['status']=='matched' for r in rows),
        certified=False,allocation_certified=False)
    return dict(result,reconciliation_hash=digest(result))


def bulk_query(cik,accession,tags):
    if (not isinstance(cik,str) or not re.fullmatch('0*[1-9][0-9]{0,9}',cik)
            or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',accession)
            or not isinstance(tags,(tuple,list)) or not 1<=len(tags)<=1000):
        raise ValueError('Invalid bounded SEC bulk parity selection')
    ciks=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
    return ("SELECT fact_id source_record,taxonomy,tag,unit,toString(value) value,toString(start_date) period_start,"
        "toString(end_date) period_end,dimensions_json,toTypeName(value) source_numeric_type FROM sec_core.sec_bulk_mirror_xbrl_fact_v3 FINAL "
        f"PREWHERE cik IN ({ciks}) AND accession_number={literal(accession)} AND tag IN ({','.join(map(literal,tags))}) LIMIT 10001")


def load_bulk(query,cik,accession,tags):
    rows=query(bulk_query(cik,accession,tags))
    if len(rows)>10000:raise ValueError('SEC bulk parity selection exceeds 10000 facts')
    normalized=[]
    for raw in rows:
        row=dict(raw);dimensions=json.loads(row.pop('dimensions_json') or '{}')
        if not isinstance(dimensions,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in dimensions.items()):
            raise ValueError('Unsupported retained bulk dimension representation')
        row['dimensions']=tuple(sorted(dimensions.items()))
        normalized.append(row)
    return tuple(normalized)
