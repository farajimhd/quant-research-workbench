"""Reconstruct immutable extraction/reconciliation seals from normalized CH rows."""
import json
from collections import Counter
import re
from datetime import datetime,timezone

from . import sec_fundamental_publication,sec_share_capture,sec_fundamental_reconciliation
from .contracts import digest,literal


def verify(query,database,reconciliation_hash,claim,source_guard):
    if not isinstance(reconciliation_hash,str) or not re.fullmatch('[0-9a-f]{64}',reconciliation_hash):
        raise ValueError('Invalid fundamental reconciliation readback key')
    catalog=sec_fundamental_publication.tables()
    def guard():
        claim.check();source_guard();claim.check()
        sec_share_capture.verify(query,database,required=True,catalog=catalog)
    def read(table,key,value,limit=10001):
        where=f'{key}={literal(value)}'
        if limit!=2:
            columns=','.join(c.name for c in table.columns)
            census=query(f'SELECT count() n,sum(length(toJSONString(tuple({columns})))) bytes '
                f'FROM {database}.{table.name} WHERE {where}')
            if len(census)!=1 or int(census[0]['n'])>10000 or int(census[0]['bytes'])>64*1024**2:
                raise ValueError('Fundamental normalized readback exceeds row/byte bounds')
        return query(f'SELECT {",".join(c.name for c in table.columns)} FROM {database}.{table.name} '
            f'WHERE {where} '+('AND length(proof)<=8388608 ' if limit==2 else '')+
            f'ORDER BY {",".join(table.order)} LIMIT {limit}')
    guard();headers=read(catalog[-1],'reconciliation_hash',reconciliation_hash,2)
    if len(headers)!=1:raise ValueError('Fundamental completion header is missing or ambiguous')
    header=headers[0];extraction_hash=header['extraction_hash'];proof=json.loads(header['proof'])
    available=datetime.fromisoformat(proof['available_at']).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
    if (header['document_hash']!=proof['extraction_metadata']['source_text_hash']
            or header['catalog_hash']!=proof['extraction_metadata']['catalog_hash']
            or header['witness_hash']!=proof['witness_hash'] or header['qualification_hash']!=proof['qualification_hash']
            or header['precision']!=proof['precision'] or header['available_at']!=available):
        raise ValueError('Fundamental normalized header differs from sealed source metadata')
    if proof['version']!=sec_fundamental_reconciliation.VERSION:
        raise ValueError('Legacy reconciliation recipe requires explicit versioned requalification')
    groups=[read(t,'reconciliation_hash' if index==2 else 'extraction_hash',
        reconciliation_hash if index==2 else extraction_hash) for index,t in enumerate(catalog[:-1])]
    if any(len(rows)>10000 for rows in groups):raise ValueError('Fundamental readback exceeds fact bound')
    contexts,observations,comparisons=groups
    if len(observations)!=header['observations'] or len(comparisons)!=header['comparisons']:
        raise ValueError('Fundamental completion counts differ')
    by_context={};original=[];positions={};used=set()
    for row in contexts:
        context=(row['period_start'],row['period_end'],row['unit'],row['dimensions'])
        if row['context_hash']!=digest(context) or row['context_hash'] in by_context:
            raise ValueError('Fundamental context hash or uniqueness differs')
        by_context[row['context_hash']]=row
    for row in observations:
        context=by_context.get(row['context_hash'])
        if context is None or row['source_position'] in positions:raise ValueError('Fundamental context reference or position differs')
        restored=json.loads(row['proof'])
        if restored['source_position']!=row['source_position']:raise ValueError('Fundamental source position proof differs')
        restored.update(concept=row['concept'],value=row['value'],unit=context['unit'],period_start=context['period_start'],
            period_end=context['period_end'],dimensions=context['dimensions'])
        original.append(restored);positions[row['source_position']]=row['value'];used.add(row['context_hash'])
    if used!=set(by_context):raise ValueError('Fundamental source has unreferenced contexts')
    extraction=dict(proof['extraction_metadata'],observations=original,rejected=proof['rejected_original'])
    sealed=extraction.pop('extraction_hash')
    if sealed!=extraction_hash or digest(extraction)!=sealed:raise ValueError('Reconstructed fundamental extraction seal differs')
    restored_comparisons=[];seen=set()
    for row in comparisons:
        if row['source_record'] in seen or any(p not in positions for p in row['original_positions']):
            raise ValueError('Fundamental comparison identity or source reference differs')
        seen.add(row['source_record'])
        restored={k:v for k,v in row.items() if k not in ('extraction_hash','source_archive_date','reconciliation_hash')}
        restored['original_values']=tuple(positions[p] for p in row['original_positions'])
        restored_comparisons.append(restored)
    reconciliation={k:v for k,v in proof.items() if k not in ('extraction_metadata','reconciliation_hash')}
    reconciliation['comparisons']=restored_comparisons
    if digest(reconciliation)!=reconciliation_hash or proof['reconciliation_hash']!=reconciliation_hash:
        raise ValueError('Reconstructed fundamental reconciliation seal differs')
    guard()
    if read(catalog[-1],'reconciliation_hash',reconciliation_hash,2)!=headers:
        raise ValueError('Fundamental completion changed during readback')
    for table,before,index in zip(catalog[:-1],groups,range(3)):
        if read(table,'reconciliation_hash' if index==2 else 'extraction_hash',reconciliation_hash if index==2 else extraction_hash)!=before:
            raise ValueError('Fundamental normalized rows changed during readback')
    statuses=dict(sorted(Counter(row['status'] for row in restored_comparisons).items()))
    blockers={status:count for status,count in statuses.items() if status!='matched'}
    rejected=len(proof['rejected_original']);unmatched=len(proof['unmatched_original_positions'])
    unmatched_positions=set(proof['unmatched_original_positions'])
    unmatched_contexts=dict(sorted(Counter('dimensioned' if row['dimensions'] else 'unscoped'
        for row in original if row['source_position'] in unmatched_positions).items()))
    cover_rejected=len(proof['extraction_metadata']['security_cover']['rejected'])
    if rejected:blockers['rejected_original_numeric_facts']=rejected
    if unmatched:blockers['unmatched_original_numeric_facts']=unmatched
    if cover_rejected:blockers['rejected_original_cover_facts']=cover_rejected
    if not comparisons:blockers['missing_bulk_comparison_census']=1
    return dict(readback_verified=True,extraction_hash=extraction_hash,reconciliation_hash=reconciliation_hash,
        observations=len(observations),comparisons=len(comparisons),all_bulk_matched=proof['all_bulk_matched'],
        comparison_status_counts=statuses,parity_input_blockers=blockers,parity_inputs_ready=not blockers,
        unmatched_original_context_counts=unmatched_contexts,
        certified=False,allocation_certified=False)
