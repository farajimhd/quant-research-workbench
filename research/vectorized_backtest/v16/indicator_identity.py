"""Frozen input-bank listing scope over a fully verified preopen snapshot."""
from datetime import datetime,time,timezone
from research.rl_trading.v1.common import NY,digest
from research.vectorized_backtest.v4.torch_backtest.source import arte_sql as sql


def population_map(client,source,day,expected):
    saved=[p for p in source['definition']['plan']['population'] if p['session_date']==str(day)]
    if len(saved)!=1:raise ValueError('Missing unique source population certificate')
    saved=saved[0];cert=saved['certificate']
    if (cert['status'],cert['revision']) not in {('certified','preopen-tradable-snapshot-v3'),('carried_forward','preopen-tradable-carry-forward-v1')}:
        raise ValueError('Unsupported preopen snapshot')
    if cert['status']=='carried_forward' and not source['definition']['population_authority']['allow_carried_forward']:raise ValueError('Unapproved carried-forward population')
    utc=lambda text:datetime.fromisoformat(text).replace(tzinfo=timezone.utc) if datetime.fromisoformat(text).tzinfo is None else datetime.fromisoformat(text)
    cutoff=datetime.combine(day,time(4),NY)
    if not utc(cert['captured_at_utc'])<=utc(cert['available_at_utc'])<cutoff or utc(cert['cutoff_utc'])!=cutoff:raise ValueError('Noncausal preopen snapshot')
    where=f'session_date={sql.literal(day)} AND snapshot_id={sql.literal(cert["snapshot_id"])}'
    proof=sql.query(client,'SELECT count() AS n,countIf(is_tradable=1) AS tradable,'
        'sum(cityHash64(tuple(ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,captured_at_utc))) AS source_hash '
        f'FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where}')[0]
    if any(int(proof[a])!=int(cert[b]) for a,b in (('n','row_count'),('tradable','tradable_count'),('source_hash','source_hash'))):raise ValueError('Pinned population count/content changed')
    members=sql.query(client,'SELECT ticker,symbol_id,listing_id,security_id,source_run_id,captured_at_utc AS inserted_at '
        f'FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where} AND is_tradable=1 ORDER BY ticker,symbol_id,listing_id')
    if digest(members)!=saved['snapshot_hash']:raise ValueError('Pinned population identity hash changed')
    # Scope is the immutable input bank's mapping, not a new venue filter or a
    # first-row choice among aliases. Every named listing must match exactly once.
    chosen={}
    for row in members:
        identity=row['listing_id']
        if identity not in expected:continue
        if identity in chosen or not identity or expected[identity]!=row['ticker']:raise ValueError('Ambiguous or changed certified listing pair')
        if row['ticker'] not in source['units'][str(day)]:raise ValueError('Listing lacks a certified bar unit')
        chosen[identity]=row['ticker']
    if set(chosen)!=set(expected):raise ValueError('Frozen input listing missing from certified preopen snapshot')
    return chosen,dict(snapshot_sha256=saved['snapshot_hash'],snapshot_rows=len(members),
        frozen_scope_rows=len(chosen),outside_frozen_scope_rows=len(members)-len(chosen),scope='existing certified input-bank listing mapping')
