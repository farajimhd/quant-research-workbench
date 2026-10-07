"""Pure source-owned structural preparation; no financial run or saved context."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from weakref import WeakKeyDictionary

import polars as pl
import pyarrow as pa

from src.market_engine.completed_endpoint_return_contract import table_hash
from src.market_engine.structural_decision_population_contract import (
    StructuralSourcePlan,StructuralPopulationPacket,certify_structural_sources,implementation_hash,partition_ticker_tables,
    ROW_TABLE,WITNESS_TABLE,COUNT_TABLE,COVERAGE_TABLE,CERTIFICATE_TABLE,
    ROW_SCHEMA,WITNESS_SCHEMA,COUNT_SCHEMA,COVERAGE_SCHEMA,CERTIFICATE_SCHEMA,MAX_DECISIONS,
)
_ISSUED=WeakKeyDictionary()


def _packet_binding(packet):
    return (packet.source.token,implementation_hash(),tuple(table_hash(table) for table in
        (packet.rows,packet.witnesses,packet.counts,packet.coverage,packet.certificate)))


@dataclass(frozen=True,slots=True)
class ProducerStructuralContext:
    observations: object
    market: object
    v7: object
    pivots: object
    tick_int: int
    stop_buffer_ticks: int
    break_buffer_ticks: int
    gate_policy: object
    geometry_policy: object


class ProducerStructuralAuthority:
    """Narrow genuine producer source interface consumed by existing preparation."""
    def __init__(self,source,reader):
        self.source,self.reader=source,reader
        self.tickers=frozenset(source.market.tickers)
        self.session_date=date.fromisoformat(source.market.sessions[0])
        self.source_end=source.declaration.end_boundary_ms
        self.certified_scan=source.scan

    def contexts_for(self,tickers):
        from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
        from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
        from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
        if (type(tickers) is not tuple or not 1<=len(tickers)<=8
                or tickers!=tuple(sorted(set(tickers))) or not set(tickers)<=self.tickers):
            raise ValueError('Structural contexts require bounded certified membership')
        s,d=self.source,self.source.declaration
        v7=certify_v7_interval_plan(s.market,s.seeds,session_date=s.market.sessions[0],
            candidate_tickers=tickers,client=self.reader)
        pivots=certify_pivot_plan(s.market,session_date=s.market.sessions[0],
            candidate_tickers=tickers,client=self.reader,batch_size=8)
        def contexts():
            # Existing native source authority reads one ticker's full history
            # at a time. Preserve that scope: eight dense100ms histories would
            # exceed the loader's1M-row cap. Generator avoids retaining all8.
            for ticker in tickers:
                observations,=load_ladder_observations(s.market,session_date=s.market.sessions[0],tickers=(ticker,),
                    through_boundary_ms=self.source_end,certified_scan=s.scan,policy=d.gate,client=self.reader)
                if observations.completed_source.nbytes>512*1024*1024:
                    raise RuntimeError('Structural source context exceeds512MiB; no truncation')
                yield ProducerStructuralContext(observations,s.market,v7,pivots,d.tick_int,d.stop_buffer_ticks,
                    d.break_buffer_ticks,d.gate,d.geometry)
        return contexts()


def produce_structural_population(source,reader):
    """Retain every native proposal/rejection count and every empty ticker scope."""
    from src.backend.backtest_ladder_coordinator import prepare_ladder_campaign
    if type(source) is not StructuralSourcePlan:
        raise ValueError('Structural producer requires issued typed source plan')
    verified=certify_structural_sources(source.market,source.declaration,reader)
    if verified.token!=source.token:
        raise ValueError('Structural sources changed before derivation')
    campaign=prepare_ladder_campaign(ProducerStructuralAuthority(verified,reader),
        start_boundary_ms=source.declaration.start_boundary_ms,maximum_proposals=MAX_DECISIONS)
    common=dict(build_id=source.market.build_id,session_date=date.fromisoformat(source.market.sessions[0]),attempt_id=source.attempt)
    rows,witnesses=[],[]
    for proposal in campaign.proposals:
        decision,setup=proposal.decision,proposal.decision.setup
        if decision.reason!='entry_proposed' or len(decision.target_level_ids)!=3:
            raise ValueError('Structural preparation returned invalid source decision')
        rows.append(dict(common,ticker=setup.ticker,boundary_ms=decision.boundary_ms,
            admission_boundary_ms=setup.admission_boundary_ms,qualification_boundary_ms=setup.qualification_boundary_ms,
            previous_close_int=decision.previous_close_int,close_int=decision.close_int,entry_limit_int=decision.entry_limit_int,
            resistance_int=setup.resistance.upper_comparison_int,stop_int=setup.stop.stop_int,
            resistance_id=setup.resistance.level_id,stop_pivot_id=setup.stop.pivot.pivot_id,
            target1_id=decision.target_level_ids[0],target2_id=decision.target_level_ids[1],target3_id=decision.target_level_ids[2],
            eligible_notional=proposal.eligible_notional))
        witnesses.append(dict(common,ticker=setup.ticker,boundary_ms=decision.boundary_ms,
            market_token=setup.market_plan_token,scan_hash=setup.scan_content_hash,v7_token=setup.v7_plan_token,
            pivot_token=setup.pivot_plan_token,source_plan_token=source.token,declaration_digest=source.declaration.digest,
            source_row_index=setup.source_row_index,resistance_confirmed_epoch_ms=setup.resistance.confirmed_at_ms,
            qualification_vwap_bits=setup.resistance.vwap_bits,stop_pivot_boundary_ms=setup.stop.pivot.pivot_boundary_ms,
            stop_confirmed_boundary_ms=setup.stop.pivot.confirmed_boundary_ms,
            resistance_lower=setup.resistance.lower,resistance_upper=setup.resistance.upper))
    def typed(data,schema,keys):
        return pl.from_arrow(pa.Table.from_pylist(data,schema=schema)).sort(keys).to_arrow().cast(schema)
    rows=typed(rows,ROW_SCHEMA,['boundary_ms','ticker'])
    witnesses=typed(witnesses,WITNESS_SCHEMA,['boundary_ms','ticker'])
    counts=typed([dict(common,reason=reason,count=count) for reason,count in campaign.counts],COUNT_SCHEMA,['reason'])
    coverage=[]
    row_groups,witness_groups=partition_ticker_tables(rows),partition_ticker_tables(witnesses)
    empty_rows=pa.Table.from_batches([],schema=ROW_SCHEMA)
    empty_witnesses=pa.Table.from_batches([],schema=WITNESS_SCHEMA)
    for ticker in source.market.tickers:
        child=row_groups.get(ticker,empty_rows)
        witness=witness_groups.get(ticker,empty_witnesses)
        coverage.append(dict(common,ticker=ticker,decision_count=child.num_rows,rows_hash=table_hash(child),
            witnesses_hash=table_hash(witness),market_token=source.market.token,source_plan_token=source.token))
    coverage=typed(coverage,COVERAGE_SCHEMA,['ticker'])
    hashes=dict(rows_hash=table_hash(rows),witnesses_hash=table_hash(witnesses),counts_hash=table_hash(counts),coverage_hash=table_hash(coverage))
    population_hash=sha256((source.token+''.join(hashes.values())).encode()).hexdigest()
    certificate=pa.Table.from_pylist([dict(common,source_plan_token=source.token,declaration_digest=source.declaration.digest,
        implementation_hash=implementation_hash(),population_hash=population_hash,**hashes,
        ticker_count=len(source.market.tickers),decision_count=rows.num_rows,
        start_boundary_ms=source.declaration.start_boundary_ms,end_boundary_ms=source.declaration.end_boundary_ms)],schema=CERTIFICATE_SCHEMA)
    if certify_structural_sources(source.market,source.declaration,reader).token!=source.token:
        raise ValueError('Structural sources changed during derivation')
    packet=StructuralPopulationPacket(source,rows,witnesses,counts,coverage,certificate)
    from src.backend.backtest_structural_decision_population_store import validate_packet
    validate_packet(packet)
    _ISSUED[packet]=_packet_binding(packet)
    return packet


def publish_structural_population(packet,read_client,write_client,*,authority):
    """Required structural Keeper domain, full readback, coverage-last, certificate."""
    from src.market_engine.structural_decision_insert_authority import KeeperStructuralDecisionInsertAuthority
    from src.backend.backtest_structural_decision_population_store import read_table,storage_preflight,validate_packet
    from pipelines.market_sip.events.completed_return_campaign import _remaining
    if type(authority) is not KeeperStructuralDecisionInsertAuthority:
        raise ValueError('Structural publication requires exact product-specific insert authority')
    if _ISSUED.get(packet)!=_packet_binding(packet):
        raise ValueError('Structural publication requires exact unchanged producer-issued packet')
    validate_packet(packet)
    s=packet.source
    with authority.ownership(s.attempt) as lease:
        if certify_structural_sources(s.market,s.declaration,read_client).token!=s.token:
            raise ValueError('Structural source certification changed before publication')
        storage_preflight(read_client)
        expected={ROW_TABLE:packet.rows,WITNESS_TABLE:packet.witnesses,COUNT_TABLE:packet.counts,
            COVERAGE_TABLE:packet.coverage,CERTIFICATE_TABLE:packet.certificate}
        existing={table:read_table(read_client,table,s) for table in expected}
        lease.admit_existing(any(table.num_rows for table in existing.values()))
        remaining={}
        for table,rows in expected.items():
            keys=['boundary_ms','ticker'] if table in (ROW_TABLE,WITNESS_TABLE) else ['reason'] if table==COUNT_TABLE else ['ticker'] if table==COVERAGE_TABLE else ['attempt_id']
            remaining[table]=_remaining(existing[table],rows,keys)
        if (existing[COVERAGE_TABLE].num_rows or existing[CERTIFICATE_TABLE].num_rows) and any(
                remaining[t].num_rows for t in (ROW_TABLE,WITNESS_TABLE,COUNT_TABLE)):
            raise ValueError('Structural coverage/certification preceded complete normalized children')
        if existing[CERTIFICATE_TABLE].num_rows:
            if any(t.num_rows for t in remaining.values()):
                raise ValueError('Installed structural certification has incomplete children')
            lease.complete(packet.certificate['population_hash'][0].as_py())
            return 'skipped'
        for table,rows in expected.items():
            def verify(table=table,rows=rows):
                if table_hash(read_table(read_client,table,s))!=table_hash(rows):
                    raise ValueError('Structural typed insertion/readback incomplete')
            missing=remaining[table]
            if missing.num_rows:
                storage_preflight(read_client)
                sink=pa.BufferOutputStream()
                with pa.ipc.new_stream(sink,missing.schema) as writer: writer.write_table(missing,max_chunksize=1024)
                header=(f'INSERT INTO {table} ({",".join(missing.schema.names)}) SETTINGS max_threads=1,max_execution_time=45,'
                    'max_memory_usage=2147483648,async_insert=0 FORMAT ArrowStream\n').encode()
                lease.execute(write_client,table,header+sink.getvalue().to_pybytes(),verify)
            else: verify()
        storage_preflight(read_client)
        lease.complete(packet.certificate['population_hash'][0].as_py())
        return 'published'
