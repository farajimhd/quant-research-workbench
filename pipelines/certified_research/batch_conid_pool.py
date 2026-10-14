"""Pinned research identity pool; dated eligibility remains a separate authority.

This consumes completed case-review receipts, never a latest-row heuristic.
It neither updates Reference Gateway nor projects current listing status backward.
"""
from collections import Counter, defaultdict
from datetime import date
import json
import re

from .contracts import digest, literal
from . import conid_case_review as review, conid_reconciliation as reconciliation


def read_review(query, review_hash, start, end):
    if not isinstance(review_hash, str) or not re.fullmatch('[0-9a-f]{64}', review_hash):
        raise ValueError('An explicit sealed conid review hash is required')
    review.verify(query, 'q_live', required=True)
    where='review_hash='+literal(review_hash)
    headers=query(f'SELECT DISTINCT * FROM q_live.{review.receipt_table().name} WHERE {where} LIMIT 2')
    if len(headers)!=1:
        raise ValueError('Conid review completion receipt is missing or competing')
    header=headers[0]
    if header['record_hash']!=digest({k:v for k,v in header.items() if k!='record_hash'}):
        raise ValueError('Conid review receipt hash differs')
    first=date.fromisoformat(str(header['range_start']));last=date.fromisoformat(str(header['range_end']))
    if header['version']!=review.VERSION or not first<=start<=end<=last:
        raise ValueError('Conid review version/range is incompatible; publish a review for this target range')
    rows=query(f'SELECT DISTINCT * FROM q_live.{review.table().name} WHERE {where} ORDER BY entity_key LIMIT 2001')
    for row in rows:
        row['conid']=int(row['conid']);row['candidate_conids']=list(map(int,row['candidate_conids']))
    if (len(rows)!=int(header['reviewed']) or len(rows)>2000
            or len({r['entity_key'] for r in rows})!=len(rows)
            or digest(rows)!=header['rows_hash']
            or any(r['record_hash']!=digest({k:v for k,v in r.items() if k not in ('record_hash','review_hash')}) for r in rows)
            or sum(r['decision']=='identity_resolved' for r in rows)!=int(header['identity_resolved'])
            or sum(r['decision']=='blocked' for r in rows)!=int(header['blocked'])
            or int(header['identity_resolved'])+int(header['blocked'])!=len(rows)
            or any(r['review_hash']!=review_hash or r['decision'] not in ('identity_resolved','blocked')
                or (r['decision']=='identity_resolved' and not 0<r['conid']<2**64)
                or (r['decision']=='blocked' and r['conid']!=0) for r in rows)):
        raise ValueError('Conid review decision census/hash differs')
    decisions=[{k:v for k,v in row.items() if k!='review_hash'} for row in rows]
    if digest(dict(version=header['version'],baseline=header['baseline_hash'],
            evidence_hash=header['source_evidence_hash'],decisions=decisions))!=review_hash:
        raise ValueError('Conid review content fingerprint differs')
    hashes=sorted({r['evidence_hash'] for r in rows})
    for offset in range(0,len(hashes),128):
        packet=hashes[offset:offset+128];ids=','.join(map(literal,packet))
        proofs=query(f'SELECT DISTINCT * FROM q_live.{review.evidence_table().name} WHERE evidence_hash IN ({ids}) LIMIT 129')
        if (len(proofs)!=len(packet) or {p['evidence_hash'] for p in proofs}!=set(packet)
                or any(digest(json.loads(p['payload']))!=p['evidence_hash'] for p in proofs)):
            raise ValueError('Conid review original evidence is missing or competing')
    if query(f'SELECT DISTINCT * FROM q_live.{review.receipt_table().name} WHERE {where} LIMIT 2')!=headers:
        raise ValueError('Conid review receipt changed during readback')
    return dict(header=header,rows=rows)


def compile_pool(current, sealed, event_tickers):
    """Merge exact provider identities; retain inactive/OTC and every rejection."""
    decisions={r['entity_key']:r for r in sealed['rows']}
    pool=[]
    for native in current['rows']:
        decision=decisions.get(native['entity_key'])
        supported=decision is not None and decision['decision']=='identity_resolved'
        veto=decision is not None and decision['decision']=='blocked'
        if supported and native['status']=='resolved' and (
                native['conid']!=decision['conid'] or native['security_id']!=decision['security_id']):
            raise ValueError('Reviewed conid conflicts with the current canonical identity')
        selected=decision if supported else native
        pool.append(dict(entity_key=native['entity_key'],ticker=native['ticker'],
            provider_type=native['provider_type'],provider_active=native['provider_active'],
            provider_exchange=native['provider_exchange'],security_id=selected['security_id'],
            conid=int(selected['conid']) if not veto and (supported or native['status']=='resolved') else 0,
            listing_ids=selected['listing_ids'],status='resolved' if not veto and (supported or native['status']=='resolved') else 'blocked',
            reason='' if supported else decision['reason'] if veto else native['reason'],
            remaining_issues=decision['remaining_issues'] if supported else [],
            evidence_hash=decision['evidence_hash'] if veto else selected['evidence_hash'],event_backed=native['ticker'] in event_tickers))
    if set(decisions)-{r['entity_key'] for r in pool}:
        raise ValueError('Reviewed provider identity disappeared from current inventory')
    owners=defaultdict(set)
    for row in pool:
        if row['conid']:owners[row['conid']].add(row['security_id'])
    for row in pool:
        if row['conid'] and len(owners[row['conid']])>1:
            raise ValueError('Batch pool has competing canonical securities for a conid')
    summary=dict(entities=len(pool),resolved=sum(r['status']=='resolved' for r in pool),
        blocked=sum(r['status']=='blocked' for r in pool),
        event_backed=sum(r['event_backed'] for r in pool),
        event_backed_resolved=sum(r['event_backed'] and r['status']=='resolved' for r in pool),
        resolved_tickers=len({r['ticker'] for r in pool if r['status']=='resolved'}),
        inactive_retained=sum(not r['provider_active'] for r in pool),
        remaining_issues=dict(Counter(i for r in pool for i in r['remaining_issues'])))
    unmatched=sorted(event_tickers-{r['ticker'] for r in pool})
    summary.update(event_tickers=len(event_tickers),unrepresented_event_tickers=len(unmatched))
    result=dict(version='batch-conid-pool-v1',review_hash=sealed['header']['review_hash'],
        canonical_snapshot_hash=current['snapshot_hash'],rows=pool,summary=summary,
        unrepresented_event_tickers=unmatched,historical_membership_certified=False,live_maintenance=False)
    result['pool_hash']=digest(result)
    return result


def load(query, review_hash, start, end):
    sealed=read_review(query,review_hash,start,end)
    first=date.fromisoformat(str(sealed['header']['range_start']));last=date.fromisoformat(str(sealed['header']['range_end']))
    inputs=reconciliation.load(query,first,last)
    legacy=dict(inputs,entities=[dict(r,ticker=r.get('legacy_ticker',r['ticker'])) for r in inputs['entities']])
    baseline=reconciliation.reconcile(legacy,first,last)
    # Check all retained sources, including contracts/SEC and alias evidence.
    fresh=review.review(baseline,inputs,review.load(query,baseline,inputs))
    if fresh['review_hash']!=review_hash:
        raise ValueError('Pinned conid review sources changed; publish and explicitly select a new review')
    events={r['ticker'] for r in inputs['events']
        if date.fromisoformat(str(r['first_date']))<=end and date.fromisoformat(str(r['last_date']))>=start}
    result=compile_pool(reconciliation.reconcile(inputs,first,last),sealed,events)
    bounds={r['ticker']:(str(r['first_date']),str(r['last_date'])) for r in inputs['events']}
    for row in result['rows']:
        if row['event_backed']:
            row['first_event_date'],row['last_event_date']=bounds[row['ticker']]
    result['target_start']=str(start);result['target_end']=str(end)
    result.pop('pool_hash');result['pool_hash']=digest(result)
    return result


def attach(snapshot,pool):
    """Require mapped candidates to also have complete dated membership proof."""
    admitted=defaultdict(list)
    for interval in snapshot['intervals']:
        # An interval with ineligible flags is not proof of admissible membership.
        if getattr(interval,'reason',''):continue
        admitted[(interval.listing_id,interval.security_id,interval.conid,interval.ticker)].append(interval)
    rejected=list(snapshot['rejected'])
    rejected.extend(dict(listing_id='',ticker=ticker,reason='batch_event_symbol_requires_reference_identity',
        evidence_hash=digest(dict(pool_hash=pool['pool_hash'],ticker=ticker)))
        for ticker in pool['unrepresented_event_tickers'])
    for row in pool['rows']:
        if not row['event_backed'] or row['provider_type'] not in ('CS','ADRC'):continue
        reason=''
        if row['status']!='resolved':reason='batch_conid_unresolved'
        elif row['remaining_issues']:reason='batch_identity_dependencies_unresolved'
        elif not any((listing,row['security_id'],row['conid'],row['ticker']) in admitted for listing in row['listing_ids']):
            reason='batch_candidate_requires_dated_membership'
        elif 'first_event_date' in row:
            from datetime import datetime,time,timedelta,timezone
            from zoneinfo import ZoneInfo
            first=max(date.fromisoformat(row['first_event_date']),date.fromisoformat(pool['target_start']))
            last=min(date.fromisoformat(row['last_event_date']),date.fromisoformat(pool['target_end']))
            ny=ZoneInfo('America/New_York')
            cursor=datetime.combine(first,time(),ny).astimezone(timezone.utc)
            horizon=datetime.combine(last+timedelta(days=1),time(),ny).astimezone(timezone.utc)
            intervals=sorted((i for listing in row['listing_ids'] for i in admitted.get(
                (listing,row['security_id'],row['conid'],row['ticker']),())),key=lambda i:i.valid_from)
            for interval in intervals:
                if interval.valid_from>cursor:break
                cursor=max(cursor,interval.valid_to)
            if cursor<horizon:reason='batch_candidate_has_dated_coverage_gap'
        if reason:rejected.append(dict(listing_id=row['listing_ids'][0] if row['listing_ids'] else '',
            ticker=row['ticker'],entity_key=row['entity_key'],reason=reason,evidence_hash=row['evidence_hash']))
    result=dict(snapshot,rejected=tuple(rejected),ready=not rejected,
        rejection_counts=dict(Counter(r['reason'] for r in rejected)),
        conid_pool=dict(pool_hash=pool['pool_hash'],review_hash=pool['review_hash'],summary=pool['summary']))
    result['source_hash']=digest(dict(dated_source_hash=snapshot['source_hash'],conid_pool_hash=pool['pool_hash']))
    return result
