"""SELECT-only content audit of complete producer broker units for V4 research.

This creates no app-release fence and grants no live or app Backtest authority.
The certificate binds the producer ledger and every selected physical unit.
"""
from contextlib import closing
from dataclasses import dataclass,asdict
from pathlib import Path
import sqlite3
from .source.arte_sql import query,literal
from .source.bracket_source import assert_liquidity_storage

@dataclass(frozen=True)
class BrokerUnit:
    ticker:str
    stage:str
    attempt_id:str
    source_hash:str
    output_rows:int
    output_hash:str

@dataclass(frozen=True)
class OfflineBrokerCertificate:
    build_id:str
    sessions:tuple
    units:tuple
    token:str
    evidence:dict

def certify_broker_units(reader,source,ledger,day,tickers,progress=None):
    """Verify full unit keys/content, including rows outside the replay window."""
    from research.rl_trading.v1.common import digest
    assert_liquidity_storage(reader)
    path=Path(ledger).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT ticker,attempt_id,source_hash,output_rows,output_hash,status FROM units WHERE build_id=? AND session_date=? AND stage=?',
            (source['build_id'],str(day),'broker_100ms')).fetchall()
    records={r[0]:r for r in rows}
    if len(records)!=len(rows):raise ValueError('Duplicate producer broker unit')
    units=[]
    for ticker in tickers:
        row=records.get(ticker)
        if row is None or row[-1]!='complete':raise ValueError('Incomplete producer broker unit: '+ticker)
        if row[2]!=source['units'][str(day)][ticker]['bars']['source_hash']:
            raise ValueError('Broker/feature canonical source fingerprint mismatch: '+ticker)
        units.append(BrokerUnit(ticker,'broker_100ms',str(row[1]),str(row[2]),int(row[3]),str(row[4])))
    for offset in range(0,len(units),128):
        group=units[offset:offset+128]
        pairs=','.join(f'({literal(u.ticker)},toUUID({literal(u.attempt_id)}))' for u in group)
        actual=query(reader,'SELECT ticker,count() AS n,uniqExact((resolution_ms,bucket_index)) AS u,sum(cityHash64(tuple(*))) AS hash '
            f'FROM arte.liquidity_100ms_v1 WHERE build_id={literal(source["build_id"])} AND session_date=toDate({literal(day)}) '
            f'AND (ticker,attempt_id) IN ({pairs}) GROUP BY ticker')
        by_ticker={r['ticker']:r for r in actual}
        for unit in group:
            observed=by_ticker.get(unit.ticker,dict(n=0,u=0,hash=0))
            if int(observed['n'])!=unit.output_rows or int(observed['u'])!=unit.output_rows or str(observed['hash'])!=unit.output_hash:
                raise ValueError('Producer broker content/key mismatch: '+unit.ticker)
        if progress:progress(dict(stage='Audit broker content hashes',completed=min(offset+128,len(units)),total=len(units)))
    evidence=dict(version='v4-offline-broker-unit-certificate-v1',build_id=source['build_id'],day=str(day),
        definition_hash=source['definition_hash'],units=[asdict(u) for u in units],
        authority='core-complete producer manifest/ledger plus full physical count/key/content hash audit',
        app_release_fence_created=False,market_data_written=False)
    token=digest(evidence)
    return OfflineBrokerCertificate(source['build_id'],(str(day),),tuple(units),token,evidence)
