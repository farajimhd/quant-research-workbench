"""SELECT-only installed structural population proof; missing products fatal.

Never imports the producer, privately derives indicators or reconstructs absent
decision rows. Source preflight certifies full universe before any product read.
"""
from datetime import date
from hashlib import sha256
import json

import numpy as np
import polars as pl
import pyarrow as pa

from src.backend.backtest_market_data import _literal,assert_select_only,market_day_boundary
from src.backend.backtest_completed_return_campaign_store import _types
from src.market_engine.completed_endpoint_return_contract import table_hash,require_hash
from src.market_engine.structural_decision_population_contract import (
    TABLE_SCHEMAS,ROW_TABLE,WITNESS_TABLE,COUNT_TABLE,COVERAGE_TABLE,CERTIFICATE_TABLE,
    MAX_DECISIONS,StructuralPopulationPacket,StructuralSourcePlan,certify_structural_sources,
    implementation_hash,source_token,partition_ticker_tables,ROW_SCHEMA,WITNESS_SCHEMA,
)


def ddl():
    result=[]
    for table,schema in TABLE_SCHEMAS.items():
        columns=','.join(f'{name} {kind}' for name,kind in _types(schema).items())
        keys=',boundary_ms,ticker' if table in (ROW_TABLE,WITNESS_TABLE) else ',reason' if table==COUNT_TABLE else ',ticker' if table==COVERAGE_TABLE else ''
        result.append(f'CREATE TABLE IF NOT EXISTS {table} ({columns}) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) '
            f"ORDER BY (build_id,session_date,attempt_id{keys}) SETTINGS storage_policy='live_market_ssd'")
    return tuple(result)


def storage_preflight(client):
    quoted=','.join(_literal(t.split('.')[1]) for t in TABLE_SCHEMAS)
    statements=("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'",
        f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({quoted})",
        f"SELECT table,disk_name FROM system.parts WHERE active AND database='arte' AND table IN ({quoted}) AND disk_name!='live_market_ssd' LIMIT 1",
        f"SELECT table,name,type FROM system.columns WHERE database='arte' AND table IN ({quoted})")
    def catalog(sql):
        # Only these exact internal catalog SELECTs; general source guard intact.
        if sql not in statements: raise ValueError('Foreign structural metadata query')
        text=client.execute(sql+" SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,max_result_rows=128,result_overflow_mode='throw' FORMAT JSONEachRow")
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    policy,tables,parts,columns=(catalog(sql) for sql in statements)
    names={t.split('.')[1] for t in TABLE_SCHEMAS}
    if (not policy or any(r['disks']!=['live_market_ssd'] for r in policy)
            or len(tables)!=len(names) or {r['name'] for r in tables}!=names
            or any(r['storage_policy']!='live_market_ssd' for r in tables) or parts):
        raise ValueError('Structural products require exact SSD policy/tables/active part placement')
    for table,schema in TABLE_SCHEMAS.items():
        scoped=[r for r in columns if r['table']==table.split('.')[1]]
        if len(scoped)!=len(schema) or {r['name']:r['type'] for r in scoped}!=_types(schema):
            raise ValueError('Structural normalized column contract differs')


def read_table(client,table,source):
    if table not in TABLE_SCHEMAS or type(source) is not StructuralSourcePlan:
        raise ValueError('Structural read needs exact declared product and typed source')
    schema=TABLE_SCHEMAS[table]
    maximum=MAX_DECISIONS if table in (ROW_TABLE,WITNESS_TABLE) else 8192 if table==COVERAGE_TABLE else 128 if table==COUNT_TABLE else 2
    fields=','.join(f'toString({f.name}) AS {f.name}' if f.name.endswith('attempt_id') else f.name for f in schema)
    order='boundary_ms,ticker' if table in (ROW_TABLE,WITNESS_TABLE) else 'reason' if table==COUNT_TABLE else 'ticker' if table==COVERAGE_TABLE else 'attempt_id'
    sql=(f'SELECT {fields} FROM {table} WHERE build_id={_literal(source.market.build_id)} '
        f'AND session_date=toDate({_literal(source.market.sessions[0])}) AND attempt_id=toUUID({_literal(source.attempt)}) '
        f"ORDER BY {order} SETTINGS max_threads=1,max_block_size=1024,max_execution_time=45,max_memory_usage=2147483648,max_result_rows={maximum},result_overflow_mode='throw' FORMAT ArrowStream")
    assert_select_only(sql)
    stream=client.iter_arrow_record_batches(sql)
    batches,count=[],0
    try:
        for batch in stream:
            count+=batch.num_rows
            if batch.schema.remove_metadata()!=schema or batch.nbytes>16777216 or count>maximum:
                raise ValueError('Structural Arrow schema/memory/row bound differs')
            batches.append(batch.replace_schema_metadata(None))
    finally:
        close=getattr(stream,'close',None)
        if close: close()
    return pa.Table.from_batches(batches,schema=schema)


def validate_packet(packet):
    if type(packet) is not StructuralPopulationPacket or type(packet.source) is not StructuralSourcePlan:
        raise ValueError('Structural packet lacks typed producer source authority')
    s=packet.source
    if source_token(s.market,s.declaration,s.prices,s.identities,s.seeds,s.pivots,s.intervals,s.scan)!=s.token:
        raise ValueError('Structural source seal differs')
    tables={ROW_TABLE:packet.rows,WITNESS_TABLE:packet.witnesses,COUNT_TABLE:packet.counts,
        COVERAGE_TABLE:packet.coverage,CERTIFICATE_TABLE:packet.certificate}
    for table,rows in tables.items():
        if type(rows) is not pa.Table or rows.schema.remove_metadata()!=TABLE_SCHEMAS[table] or any(rows[n].null_count for n in rows.schema.names):
            raise ValueError('Structural product has lossy schema or missing required witness')
        for name,value in dict(build_id=s.market.build_id,session_date=date.fromisoformat(s.market.sessions[0]),attempt_id=s.attempt).items():
            if rows.num_rows and rows[name].unique().to_pylist()!=[value]:
                raise ValueError('Structural product identity differs')
    r,w,cov,counts=(pl.from_arrow(tables[t]) for t in (ROW_TABLE,WITNESS_TABLE,COVERAGE_TABLE,COUNT_TABLE))
    keys=r.select('boundary_ms','ticker').rows()
    if (r.height>MAX_DECISIONS or keys!=sorted(set(keys)) or keys!=w.select('boundary_ms','ticker').rows()
            or cov['ticker'].to_list()!=list(s.market.tickers) or counts['reason'].to_list()!=sorted(set(counts['reason'].to_list()))):
        raise ValueError('Structural ordering/key/whole-universe coverage differs')
    if (r.filter(~pl.col('ticker').is_in(s.market.tickers)
            | (pl.col('admission_boundary_ms')<=s.declaration.start_boundary_ms)
            | (pl.col('admission_boundary_ms')>pl.col('qualification_boundary_ms'))
            | (pl.col('qualification_boundary_ms')>=pl.col('boundary_ms'))
            | (pl.col('boundary_ms')>s.declaration.end_boundary_ms) | (pl.col('boundary_ms')%100!=0)
            | (pl.col('previous_close_int')>pl.col('resistance_int'))
            | (pl.col('close_int')<=pl.col('resistance_int')+100)
            | (pl.col('stop_int')>=pl.col('entry_limit_int')) | (pl.col('entry_limit_int')%100!=0)
            | (~pl.col('eligible_notional').is_finite()) | (pl.col('eligible_notional')<0)).height):
        raise ValueError('Structural decision violates declared completed-clock/frozen-cross geometry')
    if w.height:
        vwap=np.array(w['qualification_vwap_bits'].to_list(),dtype=np.uint64).view(np.float64)
        if not np.all(np.isfinite(vwap)&(vwap>0)):
            raise ValueError('Structural qualification VWAP witness nonfinite/unavailable')
        joined=r.join(w,on=['build_id','session_date','attempt_id','ticker','boundary_ms'])
        origin=int(market_day_boundary(s.market.sessions[0],0).timestamp())*1000
        if joined.filter((pl.col('stop_confirmed_boundary_ms')>pl.col('qualification_boundary_ms'))
                | (pl.col('stop_pivot_boundary_ms')>=pl.col('stop_confirmed_boundary_ms'))
                | (pl.col('resistance_confirmed_epoch_ms')>origin+pl.col('qualification_boundary_ms'))
                | (pl.col('market_token')!=s.market.token) | (pl.col('scan_hash')!=s.scan['authority']['content_hash'])
                | (pl.col('source_plan_token')!=s.token) | (pl.col('declaration_digest')!=s.declaration.digest)
                | (~pl.col('resistance_lower').is_finite()) | (~pl.col('resistance_upper').is_finite())
                | (pl.col('resistance_lower')<=0) | (pl.col('resistance_upper')<pl.col('resistance_lower'))
                | (pl.col('resistance_int')!=(pl.col('resistance_upper')*10000).floor().cast(pl.UInt64))).height:
            raise ValueError('Structural parent witness is future/foreign/invalid')
        for field in ('v7_token','pivot_token'):
            for value in w[field].unique(): require_hash(value)
    row_groups,witness_groups=partition_ticker_tables(packet.rows),partition_ticker_tables(packet.witnesses)
    empty_rows=pa.Table.from_batches([],schema=ROW_SCHEMA)
    empty_witnesses=pa.Table.from_batches([],schema=WITNESS_SCHEMA)
    for row in cov.iter_rows(named=True):
        ticker=row['ticker']
        child=row_groups.get(ticker,empty_rows)
        witness=witness_groups.get(ticker,empty_witnesses)
        if (row['decision_count']!=child.num_rows or row['rows_hash']!=table_hash(child)
                or row['witnesses_hash']!=table_hash(witness) or row['market_token']!=s.market.token or row['source_plan_token']!=s.token):
            raise ValueError('Structural ticker coverage seal differs')
    if packet.certificate.num_rows!=1:
        raise ValueError('Structural product lacks one installed population certificate')
    cert=packet.certificate.to_pylist()[0]
    hashes={name:table_hash(rows) for name,rows in
        [('rows_hash',packet.rows),('witnesses_hash',packet.witnesses),('counts_hash',packet.counts),('coverage_hash',packet.coverage)]}
    expected=dict(source_plan_token=s.token,declaration_digest=s.declaration.digest,implementation_hash=implementation_hash(),
        population_hash=sha256((s.token+''.join(hashes.values())).encode()).hexdigest(),**hashes,
        ticker_count=len(s.market.tickers),decision_count=r.height,start_boundary_ms=s.declaration.start_boundary_ms,
        end_boundary_ms=s.declaration.end_boundary_ms)
    if any(cert[name]!=value for name,value in expected.items()):
        raise ValueError('Structural population/count/source certificate seal differs')


def load_installed_structural_population(market,declaration,client,*,authority):
    from src.market_engine.structural_decision_insert_authority import KeeperStructuralDecisionInsertAuthority
    if type(authority) is not KeeperStructuralDecisionInsertAuthority:
        raise ValueError('Structural installed reader requires exact producer completion authority')
    source=certify_structural_sources(market,declaration,client)
    storage_preflight(client)
    tables={table:read_table(client,table,source) for table in TABLE_SCHEMAS}
    packet=StructuralPopulationPacket(source,tables[ROW_TABLE],tables[WITNESS_TABLE],tables[COUNT_TABLE],
        tables[COVERAGE_TABLE],tables[CERTIFICATE_TABLE])
    validate_packet(packet)
    authority.assert_complete(source.attempt,packet.certificate['population_hash'][0].as_py())
    return packet
