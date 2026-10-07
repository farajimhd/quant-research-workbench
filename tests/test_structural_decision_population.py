"""Run-independent producer population, native structural algorithms, typed store."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
import re

import pyarrow as pa
import pytest

from tests.test_backtest_squeeze_ladder_entry import prepared
from tests.test_backtest_squeeze_ladder_setup import plans
from tests.test_backtest_squeeze_ladder_loader import fixture as native_fixture
from tests.test_backtest_strategy_one_loader import DAY,TICKER
from test_completed_return_campaign import FakeKeeper,FixtureTerminalResolver
from src.market_engine import structural_decision_population_contract as contract
from src.market_engine.structural_decision_insert_authority import KeeperStructuralDecisionInsertAuthority
from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority,InsertAuthorityUnavailable
from pipelines.market_sip.events import structural_decision_population_producer as producer
from src.backend import backtest_structural_decision_population_store as store
from src.market_engine.completed_endpoint_return_contract import table_hash
_REAL_CONTEXTS=producer.ProducerStructuralAuthority.contexts_for
from src.backend.backtest_liquidity_price import PriceLevelPlan,PriceLevelUnit,_token
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.backend.backtest_declared_ladder_plan import CertifiedGeometryScopes


class StoreClient:
    def __init__(self):
        self.tables={name:pa.Table.from_batches([],schema=schema) for name,schema in contract.TABLE_SCHEMAS.items()}
        self.writes=[]
        self.partial=None
        self.terminal_queries={}
        self.parts=False
        self.source_scopes=[]
        self.authority=KeeperStructuralDecisionInsertAuthority(FakeKeeper())

    def execute(self,sql,*,query_id=None):
        if isinstance(sql,bytes):
            header,body=sql.split(b'\n',1)
            table=re.search(r'INSERT INTO (\S+)',header.decode()).group(1)
            rows=pa.ipc.open_stream(body).read_all()
            self.writes.append(table)
            self.terminal_queries[query_id]=sha256(sql).hexdigest()
            if self.partial==table:
                self.tables[table]=pa.concat_tables([self.tables[table],rows.slice(0,1)])
                self.partial=None
                raise TimeoutError('Original structural POST response unknown')
            self.tables[table]=pa.concat_tables([self.tables[table],rows])
            return ''
        if "getSetting('readonly')" in sql: return '1'
        if 'system.storage_policies' in sql: rows=[dict(disks=['live_market_ssd'])]
        elif 'system.tables' in sql: rows=[dict(name=t.split('.')[1],storage_policy='live_market_ssd') for t in self.tables]
        elif 'system.parts' in sql: rows=[dict(table='x',disk_name='default')] if self.parts else []
        elif 'system.columns' in sql: rows=[dict(table=t.split('.')[1],name=n,type=kind)
            for t,s in contract.TABLE_SCHEMAS.items() for n,kind in store._types(s).items()]
        else: raise AssertionError(sql)
        return '\n'.join(json.dumps(row) for row in rows)

    def iter_arrow_record_batches(self,sql):
        assert sql.startswith('SELECT ') and 'max_threads=1' in sql and 'max_execution_time=45' in sql
        table=re.search(r'FROM (\S+)',sql).group(1)
        return iter(self.tables[table].to_batches(max_chunksize=2))


@pytest.fixture
def source_fixture(monkeypatch):
    import src.backend.backtest_market_data as market_data
    import src.backend.backtest_liquidity_price as prices_module
    import src.backend.backtest_strategy_one_identity as identities_module
    import src.backend.structural_v7_seed as seeds_module
    import src.backend.backtest_declared_ladder_plan as geometry_module
    import src.backend.fixed_bar_signal as scan_module
    observed,setup,v7=prepared()
    _,market,_,pivots=plans()
    _,scan,gate,_,_=native_fixture()
    tickers=tuple(sorted((TICKER,'ZZEMPTY')))
    market=replace(market,build_id='a'*64,token='b'*64,tickers=tickers,
        units=tuple(replace(u,build_id='a'*64,ticker=t) for t in tickers for u in market.units))
    observed=replace(observed,market_plan_token=market.token,source_build_id=market.build_id)
    v7=replace(v7,source_build_id=market.build_id,token='c'*64)
    pivots=replace(pivots,source_build_id=market.build_id,token='d'*64)
    gate=replace(gate,acquisition_windows=((0,19800000),(43200000,57600000)))
    declaration=contract.StructuralDecisionDeclaration(contract.StructuralWindow.PREMARKET,gate)
    price_units=tuple(PriceLevelUnit(DAY,t,next(u.attempt_id for u in market.units if u.ticker==t and u.stage=='broker_100ms'),
        '11111111-1111-4111-8111-111111111111',1,1,1.,'e'*64) for t in tickers)
    prices=PriceLevelPlan(market.build_id,price_units,_token(market.build_id,price_units))
    identities=CertifiedIdentityPlan(market.build_id,DAY,'11111111-1111-4111-8111-111111111111',market.token,tickers,(1,2),'e'*64,'f'*64)
    # Actual typed prior seed with exact immediately preceding NYSE date.
    from src.backend.backtest_declared_ladder_seed import _previous_session
    previous=_previous_session(date.fromisoformat(DAY)).isoformat()
    seeds=CertifiedSeedPlan(market.build_id,'1'*64,tuple(dict(backtest_session=DAY,ticker=t,session_date=previous,
        source_checkpoint_hash='2'*64,source_plan_hash='3'*64,level_count=0,observation_count=0,
        input_policy='',available_at=DAY+' 00:00:00+00:00') for t in tickers),'4'*64,False)
    scopes=CertifiedGeometryScopes(((tickers,'5'*64,len(tickers),1),),len(tickers),'6'*64)
    client=StoreClient()
    def checked(m,reader):
        client.source_scopes.append(m.tickers)
    monkeypatch.setattr(market_data,'verify_market_day_plan',checked)
    monkeypatch.setattr(prices_module,'certify_price_level_plan',lambda m,c: prices)
    monkeypatch.setattr(identities_module,'certify_identity_plan',lambda m,client: identities)
    monkeypatch.setattr(seeds_module,'certified_seed_plan',lambda m,c: seeds)
    monkeypatch.setattr(geometry_module,'_geometry_scopes',lambda *args:(scopes,scopes))
    monkeypatch.setattr(scan_module,'load_first_squeeze_occurrences',lambda *args,**kwargs:scan)
    def contexts(self,tickers):
        assert tickers==(TICKER,)
        return (producer.ProducerStructuralContext(observed,market,v7,pivots,100,1,1,gate,None),)
    monkeypatch.setattr(producer.ProducerStructuralAuthority,'contexts_for',contexts)
    source=contract.certify_structural_sources(market,declaration,client)
    return client,source,observed,v7,pivots


def test_real_native_preparation_issues_compact_keys_witnesses_and_empty_coverage(source_fixture):
    client,source,*_=source_fixture
    packet=producer.produce_structural_population(source,client)
    assert packet.rows.num_rows==1
    assert packet.rows['boundary_ms'].to_pylist()==[65100]
    assert packet.rows['target1_id'].to_pylist()==['R2']
    assert packet.coverage['ticker'].to_pylist()==list(source.market.tickers)
    assert packet.coverage['decision_count'].to_pylist()==[1,0]
    counts=dict(zip(packet.counts['reason'].to_pylist(),packet.counts['count'].to_pylist()))
    assert counts['entry_proposed']==counts['setup_qualified']==1
    assert counts['no_session_admission_tickers']==1
    assert all(scope==source.market.tickers for scope in client.source_scopes)
    assert not hasattr(source,'run_id') and not hasattr(source,'configuration')
    producer.publish_structural_population(packet,client,client,authority=client.authority)
    loaded=store.load_installed_structural_population(source.market,source.declaration,client,authority=client.authority)
    assert loaded.certificate.equals(packet.certificate)
    assert producer.publish_structural_population(packet,client,client,authority=client.authority)=='skipped'


def test_ah_rth_warmup_kept_but_no_pre16_admission_carry(source_fixture):
    client,source,*_=source_fixture
    ah=replace(source.declaration,window=contract.StructuralWindow.AFTERHOURS)
    issued=contract.certify_structural_sources(source.market,ah,client)
    packet=producer.produce_structural_population(issued,client)
    assert issued.declaration.end_boundary_ms==57600000
    assert packet.rows.num_rows==0
    assert packet.coverage['decision_count'].to_pylist()==[0,0]
    counts=dict(zip(packet.counts['reason'].to_pylist(),packet.counts['count'].to_pylist()))
    assert counts['admission_outside_requested_session']==1
    assert counts['no_session_admission_tickers']==2


def test_installed_reader_never_calls_producer_or_fabricates_missing_product(source_fixture,monkeypatch):
    client,source,*_=source_fixture
    monkeypatch.setattr(producer,'produce_structural_population',lambda *args:pytest.fail('Reader invoked producer'))
    with pytest.raises(ValueError,match='coverage|certificate'):
        store.load_installed_structural_population(source.market,source.declaration,client,authority=client.authority)


def test_forged_and_resealed_mutated_packets_rejected_before_dispatch(source_fixture):
    client,source,*_=source_fixture
    packet=producer.produce_structural_population(source,client)
    forged=replace(packet)
    with pytest.raises(ValueError,match='producer-issued'):
        producer.publish_structural_population(forged,client,client,authority=client.authority)
    rows=packet.rows.set_column(packet.rows.schema.get_field_index('eligible_notional'),'eligible_notional',pa.array([999.]))
    mutated=replace(packet,rows=rows)
    with pytest.raises(ValueError,match='producer-issued'):
        producer.publish_structural_population(mutated,client,client,authority=client.authority)
    hashes=dict(rows_hash=table_hash(rows),witnesses_hash=table_hash(packet.witnesses),counts_hash=table_hash(packet.counts))
    coverage=packet.coverage.to_pylist()
    coverage[0]['rows_hash']=table_hash(rows)
    coverage=pa.Table.from_pylist(coverage,schema=contract.COVERAGE_SCHEMA)
    hashes['coverage_hash']=table_hash(coverage)
    cert=packet.certificate.to_pylist()[0]
    cert.update(hashes,population_hash=sha256((source.token+''.join(hashes.values())).encode()).hexdigest())
    resealed=replace(packet,rows=rows,coverage=coverage,certificate=pa.Table.from_pylist([cert],schema=contract.CERTIFICATE_SCHEMA))
    store.validate_packet(resealed)  # all scalar content seals can be forged consistently
    with pytest.raises(ValueError,match='producer-issued'):
        producer.publish_structural_population(resealed,client,client,authority=client.authority)
    original=packet.rows
    object.__setattr__(packet,'rows',rows)
    with pytest.raises(ValueError,match='producer-issued'):
        producer.publish_structural_population(packet,client,client,authority=client.authority)
    object.__setattr__(packet,'rows',original)
    assert not client.writes


def test_structural_unknown_write_blocks_until_original_terminal_resolution(source_fixture):
    client,source,*_=source_fixture
    packet=producer.produce_structural_population(source,client)
    client.partial=contract.ROW_TABLE
    with pytest.raises(TimeoutError):
        producer.publish_structural_population(packet,client,client,authority=client.authority)
    with pytest.raises(InsertAuthorityUnavailable,match='unknown/unverified'):
        producer.publish_structural_population(packet,client,client,authority=client.authority)
    client.authority.resolve_original_terminal(source.attempt,FixtureTerminalResolver(client))
    assert producer.publish_structural_population(packet,client,client,authority=client.authority)=='published'


def test_product_specific_domains_never_dispatch_foreign_tables(source_fixture):
    client,source,*_=source_fixture
    returns=KeeperProductInsertAuthority(client.authority.keeper)
    assert returns.namespace!=client.authority.namespace
    with returns.ownership(source.attempt) as lease:
        lease.admit_existing(False)
        with pytest.raises(InsertAuthorityUnavailable,match='Foreign product'):
            lease.execute(client,contract.ROW_TABLE,b'bad',lambda:None)
    from src.market_engine.completed_endpoint_return_contract import FEATURE_TABLE
    with client.authority.ownership(source.attempt) as lease:
        lease.admit_existing(False)
        with pytest.raises(InsertAuthorityUnavailable,match='Foreign product'):
            lease.execute(client,FEATURE_TABLE,b'bad',lambda:None)
    assert not client.writes


def test_source_parent_missing_full_geometry_or_bad_seed_fails_closed(source_fixture):
    _,source,*_=source_fixture
    with pytest.raises(ValueError,match='full universe'):
        replace(source,pivots=replace(source.pivots,ticker_count=1))
    with pytest.raises(ValueError,match='parent contracts'):
        replace(source,seeds=replace(source.seeds,provisional=True))


def test_source_algorithm_closure_is_explicit_and_frozen(source_fixture):
    inventory=dict(contract.source_closure_inventory())
    for name in ('squeeze_ladder_protection', 'strategy_one_pivot_timeline', 'strategy_one_bos', 'streaming_level_book'):
        assert any(path.endswith('/'+name+'.py') for path in inventory)
    assert 'ladder_profile' in inventory['src/trading_runtime/squeeze_ladder_protection.py']
    assert 'structural_ladder' in inventory['src/trading_runtime/squeeze_ladder_protection.py']
    assert 'execute_ladder_campaign' not in inventory['src/backend/backtest_ladder_coordinator.py']
    assert all("storage_policy='live_market_ssd'" in sql for sql in store.ddl())


def test_ah_context_uses_full_regular_source_warmup_and_same_prior_seed(source_fixture,monkeypatch):
    import src.backend.backtest_squeeze_ladder_loader as loader
    import src.backend.backtest_strategy_one_pivot_store as pivot_store
    import src.backend.backtest_strategy_one_v7_interval_store as v7_store
    client,source,observed,v7,pivots=source_fixture
    source=contract.certify_structural_sources(source.market,replace(source.declaration,window=contract.StructuralWindow.AFTERHOURS),client)
    calls=[]
    def load(market,**kwargs):
        calls.append(kwargs)
        return (observed,)
    def intervals(market,seeds,**kwargs):
        assert seeds is source.seeds
        return v7
    monkeypatch.setattr(loader,'load_ladder_observations',load)
    monkeypatch.setattr(pivot_store,'certify_pivot_plan',lambda *a,**k:pivots)
    monkeypatch.setattr(v7_store,'certify_v7_interval_plan',intervals)
    monkeypatch.setattr(producer.ProducerStructuralAuthority,'contexts_for',_REAL_CONTEXTS)
    authority=producer.ProducerStructuralAuthority(source,client)
    tuple(authority.contexts_for((TICKER,)))
    assert calls[0]['through_boundary_ms']==57600000
    assert calls[0]['policy'].acquisition_windows==((0,19800000),(43200000,57600000))


def test_campaign_structural_adapter_reads_installed_population_without_macd(source_fixture,monkeypatch):
    from src.market_engine.completed_return_campaign_contract import certify_native_population,DecisionSourceKind
    import src.backend.backtest_strategy_one_candidate_store as macd
    client,source,*_=source_fixture
    packet=producer.produce_structural_population(source,client)
    producer.publish_structural_population(packet,client,client,authority=client.authority)
    monkeypatch.setattr(macd,'certify_candidate_plan',lambda *a,**k:pytest.fail('MACD substitute used'))
    population=certify_native_population(source.market,client,source_kind=DecisionSourceKind.CERTIFIED_STRUCTURAL_DECISIONS,
        structural_declaration=source.declaration,structural_authority=client.authority)
    assert population.keys==((TICKER,65100),)
    assert population.candidate_token==packet.certificate['population_hash'][0].as_py()


def test_closure_rejects_unresolved_dynamic_local_dispatch(monkeypatch):
    from pathlib import Path
    original=Path.read_text
    def altered(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        if path.name=='structural_decision_population_producer.py':
            text+='\ndef unresolved_dispatch():\n    import importlib\n    return importlib.import_module(dynamic_name)\n'
        return text
    monkeypatch.setattr(Path,'read_text',altered)
    monkeypatch.setattr(contract,'_CLOSURE_CACHE',None)
    with pytest.raises(ValueError,match='Unresolved dynamic import'):
        contract.source_closure_inventory()


def test_closure_rejects_missing_local_algorithm_symbol(monkeypatch):
    from pathlib import Path
    original=Path.read_text
    def altered(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        if path.name=='backtest_squeeze_ladder_entry.py':
            text=text.replace('structural_ladder','missing_structural_ladder')
        return text
    monkeypatch.setattr(Path,'read_text',altered)
    monkeypatch.setattr(contract,'_CLOSURE_CACHE',None)
    with pytest.raises(ValueError,match='Unresolved local source symbol'):
        contract.source_closure_inventory()
