"""Verify the event-driven producer before interpreting absent buckets as empty."""
import sqlite3
from contextlib import closing
from pathlib import Path
from research.rl_trading.v1 import arte_sql as sql
from research.rl_trading.v1.common import bounds,digest
VERSION='rl-v6-certified-event-sparse-liquidity-v1'


def certify_source(source,tickers):
    """One batched integrity scan per pinned identity; no per-clock query.

    Complete broker publication follows canonical session-event conservation.
    Recheck its full persisted row count/unique keys/hash against that ledger.
    Only then is absence inside [04:00,20:00) certified no-event evidence.
    """
    names=sorted(set(tickers));cached=getattr(source,'sparse_coverage',{})
    missing=[t for t in names if t not in cached]
    if set(names)-set(source.attempts):raise ValueError('Unpinned sparse coverage identity')
    if missing:
        path=Path(source.ledger).resolve()
        uri=path.as_uri() if not str(path).startswith('\\\\') else 'file:////'+path.as_posix().lstrip('/')
        with closing(sqlite3.connect(uri+'?mode=ro',uri=True)) as db:
            rows=db.execute('SELECT ticker,attempt_id,status,output_rows,output_hash FROM units '
                'WHERE build_id=? AND session_date=? AND stage=?',
                (source.source['build_id'],str(source.day),'broker_100ms')).fetchall()
        expected={r[0]:r[1:] for r in rows}
        for offset in range(0,len(missing),128):
            group=missing[offset:offset+128]
            for t in group:
                r=expected.get(t)
                if r is None or r[0]!=source.attempts[t] or r[1]!='complete':
                    raise ValueError('Incomplete sparse producer coverage: '+t)
            scope=','.join(f'({sql.literal(t)},toUUID({sql.literal(source.attempts[t])}))' for t in group)
            query='SELECT ticker,count() AS n,uniqExact((resolution_ms,bucket_index)) AS keys,sum(cityHash64(tuple(*))) AS hash FROM arte.liquidity_100ms_v1 '
            query+=f"WHERE build_id={sql.literal(source.source['build_id'])} AND session_date=toDate({sql.literal(source.day)}) AND (ticker,attempt_id) IN ({scope}) GROUP BY ticker"
            actual={r['ticker']:r for r in sql.query(source.reader,query)}
            for t in group:
                r=actual.get(t,dict(n=0,keys=0,hash=0));e=expected[t]
                if int(r['n'])!=int(e[2]) or int(r['keys'])!=int(e[2]) or str(r['hash'])!=str(e[3]):
                    raise ValueError('Sparse producer content changed: '+t)
                cached[t]=bounds(source.day)
        source.sparse_coverage=cached
    proof=dict(version=VERSION,build_id=source.source['build_id'],day=str(source.day),
        semantics='certified_event_free_bucket_zero_volume_quote_freshness_separate',
        identities={t:dict(attempt=source.attempts[t],start_us=cached[t][0],end_us=cached[t][1]) for t in names})
    return {t:cached[t] for t in names},digest(proof)
