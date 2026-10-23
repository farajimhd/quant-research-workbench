"""Recompute compact reported projections and independently qualify retained time proof."""
import json
import re

from . import sec_reported_publication,sec_fundamental_readback,sec_witness_publication,sec_share_capture
from .contracts import digest,literal


def verify(query,database,binding_hash,claim,source_guard,*,include_source=False):
    if not isinstance(binding_hash,str) or not re.fullmatch('[0-9a-f]{64}',binding_hash):raise ValueError('Invalid reported binding locator')
    catalog=sec_reported_publication.tables()
    def guard():
        claim.check();source_guard();claim.check()
        sec_share_capture.verify(query,database,required=True,catalog=(*catalog,*sec_witness_publication.tables()))
    def read(table,key,value,*,single=False):
        columns=','.join(c.name for c in table.columns);where=f'{key}={literal(value)}'
        census=query(f'SELECT count() n,sum(length(toJSONString(tuple({columns})))) bytes FROM {database}.{table.name} WHERE {where}')
        if (len(census)!=1 or int(census[0]['n'])>(1 if single else 10000)
                or int(census[0]['bytes'])>(8 if single else 64)*1024**2):raise ValueError('Reported readback exceeds exact row/byte bounds')
        return query(f'SELECT {columns} FROM {database}.{table.name} WHERE {where} ORDER BY {",".join(table.order)} LIMIT {2 if single else 10001}')
    guard();headers=read(catalog[1],'binding_hash',binding_hash,single=True)
    if len(headers)!=1:raise ValueError('Reported completion binding is missing')
    header=headers[0]
    source=sec_fundamental_readback.verify(query,database,header['source_reconciliation_hash'],claim,source_guard,include_extraction=True)
    extraction=source['extraction']
    if source['extraction_hash']!=header['extraction_hash']:raise ValueError('Reported binding original extraction differs')
    time_table=sec_witness_publication.tables()[1]
    times=read(time_table,'qualification_hash',header['qualification_hash'],single=True)
    if len(times)!=1 or times[0]['witness_hash']!=header['witness_hash'] or times[0]['document_key']!=header['document_key']:
        raise ValueError('Reported original document/time binding differs')
    proof=json.loads(times[0]['proof']);receipt_table=sec_witness_publication.tables()[0]
    receipts=read(receipt_table,'receipt_hash',times[0]['receipt_hash'],single=True)
    if len(receipts)!=1:raise ValueError('Reported original download receipt is missing')
    receipt=json.loads(receipts[0]['proof'])
    time_expected=sec_witness_publication.records((proof['witness'],),(proof['qualification'],),receipt)
    receipt_actual=dict(receipts[0]);receipt_actual['byte_size']=int(receipt_actual['byte_size'])
    if receipt_actual!=time_expected[0][0] or times!=list(time_expected[1]):
        raise ValueError('Reported normalized original-time rows differ from source proof')
    expected=sec_reported_publication.records(extraction,proof['witness'],proof['qualification'],receipt,
        numeric_mode=header['numeric_mode'],source_reconciliation_hash=header['source_reconciliation_hash'])
    actual=read(catalog[0],'projection_hash',header['projection_hash'])
    def normal(rows):return json.loads(json.dumps(rows,sort_keys=True))
    wanted=sorted(expected[0],key=lambda row:row['context_hash'])
    if normal(actual)!=normal(wanted) or normal(headers)!=normal(expected[1]):
        raise ValueError('Reported projection differs from recomputed original source/time evidence')
    guard()
    if (read(catalog[1],'binding_hash',binding_hash,single=True)!=headers or read(catalog[0],'projection_hash',header['projection_hash'])!=actual
            or read(time_table,'qualification_hash',header['qualification_hash'],single=True)!=times
            or read(receipt_table,'receipt_hash',times[0]['receipt_hash'],single=True)!=receipts):
        raise ValueError('Reported projection or original-time evidence changed during readback')
    after=sec_fundamental_readback.verify(query,database,header['source_reconciliation_hash'],claim,source_guard,include_extraction=True)
    if after['extraction']!=extraction:raise ValueError('Reported original observation bank changed during verification')
    result=dict(readback_verified=True,binding_hash=binding_hash,projection_hash=header['projection_hash'],
        extraction_hash=header['extraction_hash'],source_reconciliation_hash=header['source_reconciliation_hash'],
        numeric_mode=header['numeric_mode'],groups=len(actual),available_at=header['available_at'],precision=header['precision'],
        public_time_qualified=True,certified=False,allocation_certified=False)
    result=dict(result,evidence_hash=digest(result))
    if include_source:
        result.update(extraction=extraction,document_key=header['document_key'],
            source_document_hash=proof['witness']['document_hash'],
            reconciliation={k:source[k] for k in ('all_bulk_matched','comparison_status_counts',
                'unmatched_original_context_counts','parity_input_blockers')})
    return result
