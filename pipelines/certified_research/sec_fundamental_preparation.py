"""Bounded retained document/time/bulk evidence capture for the unified launcher."""
import json
import sys
from collections import Counter
from pathlib import Path

from . import sec_fundamental_context,sec_fundamental_reconciliation,sec_fundamental_publication
from . import sec_document_lineage,sec_share_preparation,sec_public_archive_time,facts,sec_share_capture,sec_witness_publication
from . import sec_share_context
from . import sec_fundamental_readback
from . import sec_precision
from . import sec_reported_publication
from .contracts import digest,literal

PRODUCT='sec_fundamental_source'


def recipe_hash():
    modules=(sys.modules[__name__],sec_fundamental_context,sec_fundamental_reconciliation,sec_fundamental_publication,
             sec_document_lineage,sec_share_preparation,sec_public_archive_time,facts,sec_share_context,sec_fundamental_readback,sec_precision)
    return digest(dict(version='sec-original-fundamental-preparation-v1',
        algorithms={m.__name__:digest(Path(m.__file__).read_text(encoding='utf-8')) for m in modules},
        catalog_hash=facts.catalog_hash(),ddl=tuple(t.ddl('q_live') for t in sec_fundamental_publication.tables())))


def reported_recipe_hash(numeric_mode):
    if numeric_mode not in ('round-to-nearest','truncation'):raise ValueError('Explicit reported projection numeric mode is required')
    return digest(dict(source_recipe=recipe_hash(),numeric_mode=numeric_mode,
        publisher=digest(Path(sec_reported_publication.__file__).read_text(encoding='utf-8')),
        ddl=tuple(t.ddl('q_live') for t in sec_reported_publication.tables())))


def prepare(query,config,requests,claim,dictionary_factory,profile,*,mode,stop,progress=None,numeric_mode=None):
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
    reported_admission=None
    if numeric_mode is not None:
        reported_admission=sec_share_preparation.admission(query,profile,len(requests),1,
            product='sec_reported_projection',recipe=reported_recipe_hash(numeric_mode))
        reported_admission(0)
    sec_share_capture.verify(query,'q_live',required=True,catalog=sec_witness_publication.tables())
    dictionary=dictionary_factory()
    try:
        sec_fundamental_publication.install(query,'q_live',dictionary)
        if numeric_mode is not None:sec_reported_publication.install(query,'q_live',dictionary)
    finally:dictionary.close()
    tags=tuple(sorted({tag for metric in facts.catalog() for tag in metric.tags}));completed=[];reconciliations=[];inserted=0;matched=0
    blockers=Counter();unmatched_contexts=Counter();parity_ready=0;reported_bindings=[];reported_inserted=0
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
        expected=sec_fundamental_reconciliation.reconcile(extraction,bulk,witness,qualified,receipt)['reconciliation_hash']
        if result['reconciliation_hash']!=expected:raise ValueError('Captured fundamental reconciliation differs from current inputs')
        verified=sec_fundamental_readback.verify(query,'q_live',expected,claim,source_guard)
        if (verified.get('readback_verified') is not True or verified.get('extraction_hash')!=extraction['extraction_hash']
                or verified.get('reconciliation_hash')!=expected):
            raise ValueError('Independent fundamental readback did not verify current source seals')
        matched+=int(verified['all_bulk_matched']);reconciliations.append(expected)
        blockers.update(verified['parity_input_blockers']);parity_ready+=int(verified['parity_inputs_ready'])
        unmatched_contexts.update(verified['unmatched_original_context_counts'])
        if numeric_mode is not None:
            projected=sec_reported_publication.capture(query,'q_live',extraction,witness,qualified,receipt,claim,source_guard,
                reported_admission,numeric_mode=numeric_mode,source_reconciliation_hash=expected)
            reported_bindings.append(projected['binding_hash'])
            reported_inserted+=projected['inserted_rows']
        inserted+=result['inserted_rows'];completed.append(result['extraction_hash'])
        if progress:progress(dict(phase='fundamental-evidence',completed=len(completed),total=len(requests),inserted_rows=result['inserted_rows'],
            readback_verified=True,all_bulk_matched=verified['all_bulk_matched'],parity_inputs_ready=verified['parity_inputs_ready']))
    return dict(status='complete',completed=tuple(completed),reconciliations=tuple(reconciliations),inserted_rows=inserted,
        readback_verified=True,matched_documents=matched,unmatched_documents=len(completed)-matched,
        parity_inputs_ready=parity_ready==len(completed),parity_ready_documents=parity_ready,
        parity_input_blockers=dict(sorted(blockers.items())),unmatched_original_context_counts=dict(sorted(unmatched_contexts.items())),
        numeric_mode=numeric_mode,reported_bindings=tuple(reported_bindings),reported_inserted_rows=reported_inserted,
        certified=False,allocation_certified=False)
