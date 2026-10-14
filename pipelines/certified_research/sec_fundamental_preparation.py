"""Bounded retained document/time/bulk evidence capture for the unified launcher."""
import json
import sys
from pathlib import Path

from . import sec_fundamental_context,sec_fundamental_reconciliation,sec_fundamental_publication
from . import sec_document_lineage,sec_share_preparation,sec_public_archive_time,facts,sec_share_capture,sec_witness_publication
from . import sec_share_context
from .contracts import digest,literal

PRODUCT='sec_fundamental_source'


def recipe_hash():
    modules=(sys.modules[__name__],sec_fundamental_context,sec_fundamental_reconciliation,sec_fundamental_publication,
             sec_document_lineage,sec_share_preparation,sec_public_archive_time,facts,sec_share_context)
    return digest(dict(version='sec-original-fundamental-preparation-v1',
        algorithms={m.__name__:digest(Path(m.__file__).read_text(encoding='utf-8')) for m in modules},
        catalog_hash=facts.catalog_hash(),ddl=tuple(t.ddl('q_live') for t in sec_fundamental_publication.tables())))


def prepare(query,config,requests,claim,dictionary_factory,profile,*,mode,stop,progress=None):
    if not isinstance(requests,(tuple,list)) or not 1<=len(requests)<=1000 or len(set(requests))!=len(requests):
        raise ValueError('Fundamental preparation requires up to 1000 unique documents')
    if mode not in ('acceptance_checked_day','official_filing_day'):
        raise ValueError('Explicit original fundamental public-time mode is required')
    if config.global_memory_bytes<sec_share_preparation.MEMORY+config.query_memory_bytes:
        raise ValueError('Fundamental preparation requires 2GiB plus query memory')
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('Original fundamental preparation stopped')
    admit=sec_share_preparation.admission(query,profile,len(requests),1,product=PRODUCT,recipe=recipe_hash())
    guard();admit(0)
    sec_share_capture.verify(query,'q_live',required=True,catalog=sec_witness_publication.tables())
    dictionary=dictionary_factory()
    try:sec_fundamental_publication.install(query,'q_live',dictionary)
    finally:dictionary.close()
    tags=tuple(sorted({tag for metric in facts.catalog() for tag in metric.tags}));completed=[];inserted=0
    from .sec_share_source import VERSION as source_version
    from .sec_share_context import VERSION as parser_version
    for cik,accession,name in requests:
        guard();lineage=sec_document_lineage.inspect(query,cik,accession,name,claim,guard)
        key=digest(dict(version=source_version,candidate_hash=lineage['candidate_hash'],parser=parser_version))
        time_sql=('SELECT proof,receipt_hash FROM q_live.research_sec_document_archive_time_v1 '
            f'WHERE document_key={literal(key)} AND policy_version={literal(sec_public_archive_time.VERSION)} AND mode={literal(mode)} LIMIT 2')
        times=query(time_sql)
        if len(times)!=1:raise ValueError('Original fundamental archive-time evidence is missing or ambiguous')
        proof=json.loads(times[0]['proof']);witness=proof['witness'];qualified=proof['qualification']
        if witness['document_key']!=key or witness['candidate_hash']!=lineage['candidate_hash']:
            raise ValueError('Fundamental archive witness differs from current lineage')
        receipt_sql=('SELECT proof FROM q_live.research_sec_archive_download_receipt_v1 '
            f'WHERE receipt_hash={literal(times[0]["receipt_hash"])} LIMIT 2')
        receipts=query(receipt_sql)
        if len(receipts)!=1:raise ValueError('Original fundamental download receipt is missing or ambiguous')
        receipt=json.loads(receipts[0]['proof'])
        ciks=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
        payload=query('SELECT source_text FROM q_live.sec_filing_text_v3 '
            f'WHERE cik IN ({ciks}) AND accession_number={literal(accession)} AND document_name={literal(name)} '
            'AND length(source_text)<=16777216 LIMIT 2')
        if len(payload)!=1:raise ValueError('Original fundamental document is missing, oversized or ambiguous')
        extraction=sec_fundamental_context.extract(payload[0]['source_text'],lineage['document']['content_sha256'],cik)
        del payload
        bulk=sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags)
        bulk_hash=digest(sorted(bulk,key=lambda row:row['source_record']))
        def source_guard():
            guard()
            sec_share_capture.verify(query,'q_live',required=True,catalog=sec_witness_publication.tables())
            if query(time_sql)!=times or query(receipt_sql)!=receipts:
                raise ValueError('Original fundamental archive/time evidence changed')
            if sec_document_lineage.inspect(query,cik,accession,name,claim,guard)!=lineage:
                raise ValueError('Original fundamental lineage changed')
            current=sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags)
            if digest(sorted(current,key=lambda row:row['source_record']))!=bulk_hash:
                raise ValueError('Original fundamental bulk comparison source changed')
        result=sec_fundamental_publication.capture(query,'q_live',extraction,bulk,witness,qualified,receipt,claim,source_guard,admit)
        inserted+=result['inserted_rows'];completed.append(result['extraction_hash'])
        if progress:progress(dict(phase='fundamental-evidence',completed=len(completed),total=len(requests),inserted_rows=result['inserted_rows']))
    return dict(status='complete',completed=tuple(completed),inserted_rows=inserted,certified=False,allocation_certified=False)
