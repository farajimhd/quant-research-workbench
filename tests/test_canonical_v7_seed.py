from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from pipelines.market_sip.events.trade_reporting_flags import REVISION
from src.backend.structural_v7_seed import certified_seed_plan, load_seed, load_seeds_batch
from src.backend.backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
from src.backend.canonical_v7_seed import (selector_from_configuration,
    inspect_canonical_seed_plan, inspect_canonical_seed, CanonicalV7ExecutionUnavailable)
from src.backend.swing_book_source import HISTORICAL_POLICY, session_bounds
from src.market_engine.canonical_v7_checkpoint import (
    CanonicalV7Selector, VERSION, TABLE, checkpoint_record, empty_or_carry_checkpoint, ddl,
    initial_canonical_prior,
)
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION, StreamingLevelBook
from src.trading_runtime.journal_contract import canonical_json


def selector(through='2026-08-25'):
    plan = {'version':VERSION, 'source_policy':HISTORICAL_POLICY, 'input_policy':POLICY,
        'reporting_revision':REVISION, 'source_extraction_version':EXTRACTION_VERSION,
        'band_config_hash':digest({**CONFIG,'coverage':.8}), 'authority_start':'2026-08-24',
        'through':through, 'tickers':['TEST'], 'canonical_history_start':'2025-01-01',
        'sessions':['2026-08-24','2026-08-25']+(['2026-08-26'] if through=='2026-08-26' else []),
        'genesis_proofs':{'TEST':{'prior_canonical_event_count':0,'canonical_absence_hash':digest('verified-earlier-absence')}},
        **{key:digest(key) for key in
        ('canonical_source_plan_hash','condition_rules_hash','reporting_coverage_hash',
         'split_manifest_hash','producer_hash','numerical_kernel_hash')}}
    return CanonicalV7Selector(canonical_json(plan),digest(plan))


def receipt(selected, day, kind, parent=None, seconds=0):
    return {'ticker':'TEST', 'session_date':day, 'source_plan_hash':selected.source_plan_hash,
        'source_receipt_hash':digest(day+'source'), 'input_hash':digest(day+'input'),
        'prefix_proof_hash':digest(day+'prefix'), 'price_seconds':seconds,
        'prefix_price_seconds':seconds, 'parent_checkpoint_hash':parent,
        'kind':kind, 'split_factor':1., 'split_evidence':[]}


def empty_and_day(*, observed=False):
    selected = selector()
    first = receipt(selected,'2026-08-24','empty')
    initial = empty_or_carry_checkpoint(selected,first)
    first_record = checkpoint_record(selected,first,initial)
    second = receipt(selected,'2026-08-25','observed' if observed else 'carry',
        initial['checkpoint_hash'], 240 if observed else 0)
    if observed:
        begin,end = session_bounds('2026-08-25')
        engine = StreamingLevelBook(initial,ticker='TEST',session='2026-08-25',
            start=begin.timestamp(),end=end.timestamp())
        for i in range(240):
            price = 10 + (i%40 if i%80<40 else 40-i%40)*.01
            bar = {'t':begin.timestamp()+301+i,'open':price,'high':price+.01,
                   'low':price-.01,'close':price,'volume':100.}
            engine.update(bar,observed_at=bar['t'])
        current = engine.historical_checkpoint(second['input_hash'])
    else:
        current = empty_or_carry_checkpoint(selected,second,predecessor=initial)
    second_record = checkpoint_record(selected,second,current,predecessor=initial)
    return selected,first_record,second_record


class Reader:
    def __init__(self, selected, records):
        self.selected,self.records = selected,records
        self.queries = []
    def execute(self,query):
        self.queries.append(query)
        if query == "SELECT getSetting('readonly')":
            return '1'
        assert f'FROM {TABLE}' in query and self.selected.source_plan_hash in query
        assert ' FINAL ' not in query and 'legacy' not in query
        if 'JSONExtractString' in query:
            records = [r for r in self.records if r['checkpoint']['checkpoint_hash'] in query]
        else:
            records = [r for r in self.records if "toDate('"+r['receipt']['session_date']+"')" in query]
        return '\n'.join(canonical_json({'record_json':canonical_json(r),'record_hash':r['record_hash']}) for r in records)


def market():
    return SimpleNamespace(build_id='source-build',sessions=('2026-08-26',),
        units=(SimpleNamespace(stage='bars',session_date='2026-08-26',ticker='TEST'),))


@pytest.mark.parametrize('observed',[False,True])
def test_real_engine_inspection_cannot_become_executable_source_authority(observed):
    selected,first,current = empty_and_day(observed=observed)
    if observed:
        assert current['level_count'] > 0 and current['observation_count'] > 0
    reader = Reader(selected,[first,current])
    plan = inspect_canonical_seed_plan(market(),reader,selected)
    assert plan.executable is False
    with pytest.raises(ValueError,match='canonical and nonprovisional'):
        verify_declared_ladder_seed_plan(market(),plan)
    unit, = plan.units
    assert unit['source_checkpoint_hash'] == current['checkpoint']['checkpoint_hash']
    assert unit['session_date'] == '2026-08-25'
    seed = inspect_canonical_seed(reader,ticker='TEST',session=date(2026,8,26),coverage=unit,selector=selected)
    assert seed['source_checkpoint_hash'] == current['checkpoint']['checkpoint_hash']
    assert seed['checkpoint_hash'] == digest({key:value for key,value in seed.items() if key!='checkpoint_hash'})
    begin,end = session_bounds('2026-08-26')
    engine = StreamingLevelBook(seed,ticker='TEST',session='2026-08-26',start=begin.timestamp(),end=end.timestamp())
    assert engine.seed_input_policy == POLICY
    assert len(engine.rows) == current['level_count']
    for call in (lambda:certified_seed_plan(market(),reader,canonical_selector=selected),
                 lambda:load_seed(reader,ticker='TEST',session=date(2026,8,26),coverage=unit,canonical_selector=selected),
                 lambda:load_seeds_batch(reader,tickers=('TEST',),session=date(2026,8,26),coverage={'TEST':unit},canonical_selector=selected)):
        with pytest.raises(CanonicalV7ExecutionUnavailable,match='producer/Keeper'):
            call()
    with pytest.raises(ValueError,match='explicit sealed selector'):
        load_seed(reader,ticker='TEST',session=date(2026,8,26),coverage=unit)


def test_empty_is_nonzero_hashed_actual_engine_state_and_carry_advances_identity():
    selected,first,current = empty_and_day()
    assert first['level_count'] == current['level_count'] == 0
    assert first['checkpoint']['checkpoint_hash'] != current['checkpoint']['checkpoint_hash']
    assert current['checkpoint']['prior_checkpoint_hash'] == first['checkpoint']['checkpoint_hash']
    altered = deepcopy(current['checkpoint'])
    altered['input_policy'] = 'legacy-unfiltered'
    altered['checkpoint_hash'] = digest({key:value for key,value in altered.items() if key!='checkpoint_hash'})
    with pytest.raises(ValueError,match='source identity'):
        checkpoint_record(selected,current['receipt'],altered,predecessor=first['checkpoint'])
    bad = {**first['receipt'],'prefix_price_seconds':1}
    with pytest.raises(ValueError,match='zero full prefix'):
        empty_or_carry_checkpoint(selected,bad)
    for changed in ({**current['receipt'],'split_factor':2.},
                    {**current['receipt'],'price_seconds':1},
                    {**current['receipt'],'parent_checkpoint_hash':None}):
        with pytest.raises(ValueError):
            empty_or_carry_checkpoint(selected,changed,predecessor=first['checkpoint'])


def test_first_observed_genesis_is_bound_to_canonical_absence_and_engine():
    selected = selector()
    evidence = receipt(selected,'2026-08-24','observed',seconds=1)
    prior = initial_canonical_prior(selected,evidence)
    begin,end = session_bounds('2026-08-24')
    engine = StreamingLevelBook(prior,ticker='TEST',session='2026-08-24',start=begin.timestamp(),end=end.timestamp())
    bar = {'t':begin.timestamp()+301,'open':10.,'high':10.1,'low':9.9,'close':10.,'volume':100.}
    engine.update(bar,observed_at=bar['t'])
    record = checkpoint_record(selected,evidence,engine.historical_checkpoint(evidence['input_hash']))
    assert record['checkpoint']['prior_checkpoint_hash'] == prior['checkpoint_hash']
    with pytest.raises(ValueError,match='discard an earlier price prefix'):
        initial_canonical_prior(selected,{**evidence,'prefix_price_seconds':2})


def test_nonempty_carry_preserves_real_prior_observations_and_rejects_modified_state():
    old,first,second = empty_and_day(observed=True)
    selected = selector('2026-08-26')
    # Same numerical state; only the explicit source-plan namespace is advanced
    # in fixture evidence before a new immutable source plan is sealed.
    evidence = receipt(selected,'2026-08-26','carry',second['checkpoint']['checkpoint_hash'])
    evidence['prefix_price_seconds'] = 240
    carry = empty_or_carry_checkpoint(selected,evidence,predecessor=second['checkpoint'])
    record = checkpoint_record(selected,evidence,carry,predecessor=second['checkpoint'])
    assert record['observation_count'] == second['observation_count'] > 0
    changed = deepcopy(carry);changed['levels'][0]['price'] += 1
    changed['checkpoint_hash'] = digest({key:value for key,value in changed.items() if key!='checkpoint_hash'})
    with pytest.raises(ValueError,match='changed its predecessor state'):
        checkpoint_record(selected,evidence,changed,predecessor=second['checkpoint'])


def test_selector_is_explicit_sealed_exact_and_no_legacy_schema_declaration():
    selected = selector()
    config = SimpleNamespace(payload={'strategy':{'numbered_release':{'canonical_v7_source':selected.payload()}}})
    assert selector_from_configuration(config) == selected
    assert 'ORDER BY (source_plan_hash,session_date,ticker)' in ddl()
    assert "storage_policy='live_market_ssd'" in ddl()
    assert 'structural_level_coverage_v7' not in ddl()
    for field,value in [('input_policy','legacy-unfiltered'),('tickers',['TEST','TEST']),
                        ('reporting_revision','unknown'),('producer_hash','0'*64),('extra',1)]:
        plan = selected.payload()['plan'];plan[field]=value
        with pytest.raises(ValueError):
            CanonicalV7Selector(canonical_json(plan),digest(plan)).plan()
    for value in ([],['2026-08-25']):
        plan = selected.payload()['plan'];plan['sessions'] = value
        with pytest.raises(ValueError,match='every NYSE session'):
            CanonicalV7Selector(canonical_json(plan),digest(plan)).plan()
    plan = selected.payload()['plan'];plan['genesis_proofs']['TEST']['prior_canonical_event_count'] = 1
    with pytest.raises(ValueError,match='all earlier events'):
        CanonicalV7Selector(canonical_json(plan),digest(plan)).plan()
    later = receipt(selected,'2026-08-25','empty')
    with pytest.raises(ValueError,match='cannot restart empty'):
        empty_or_carry_checkpoint(selected,later)


def test_missing_duplicate_corrupt_stale_and_changed_predecessor_fail_closed():
    selected,first,current = empty_and_day()
    for records in ([first],[first,current,current]):
        with pytest.raises(ValueError,match='missing or duplicated'):
            inspect_canonical_seed_plan(market(),Reader(selected,records),selected)
    bad = deepcopy(current);bad['checkpoint']['available_at'] += 1
    bad['record_hash'] = digest({key:value for key,value in bad.items() if key!='record_hash'})
    with pytest.raises(ValueError):
        inspect_canonical_seed_plan(market(),Reader(selected,[first,bad]),selected)
    stale = deepcopy(first)
    stale['checkpoint']['input_policy'] = 'legacy-unfiltered'
    stale['checkpoint']['checkpoint_hash'] = digest({key:value for key,value in stale['checkpoint'].items() if key!='checkpoint_hash'})
    stale['record_hash'] = digest({key:value for key,value in stale.items() if key!='record_hash'})
    with pytest.raises(ValueError,match='predecessor'):
        inspect_canonical_seed_plan(market(),Reader(selected,[stale,current]),selected)
    reader = Reader(selected,[first,current])
    plan = inspect_canonical_seed_plan(market(),reader,selected)
    mutated = deepcopy(current);mutated['receipt']['source_receipt_hash'] = digest('changed-source')
    mutated['record_hash'] = digest({key:value for key,value in mutated.items() if key!='record_hash'})
    reader.records = [first,mutated]
    with pytest.raises(ValueError,match='changed after preflight'):
        inspect_canonical_seed(reader,ticker='TEST',session=date(2026,8,26),coverage=plan.units[0],selector=selected)


def test_cached_validated_plan_is_deeply_immutable_and_parent_query_is_date_bounded():
    selected,first,current = empty_and_day()
    plan = selected.plan()
    assert selected.plan() is plan
    with pytest.raises(TypeError):
        plan['genesis_proofs']['TEST']['prior_canonical_event_count'] = 1
    mutable = selected.payload()['plan']
    mutable['genesis_proofs']['TEST']['prior_canonical_event_count'] = 7
    assert selected.plan()['genesis_proofs']['TEST']['prior_canonical_event_count'] == 0
    reader = Reader(selected,[first,current])
    inspect_canonical_seed_plan(market(),reader,selected)
    queries = [query for query in reader.queries if query.startswith('SELECT record_json')]
    assert len(queries) == 2
    assert "session_date=toDate('2026-08-24')" in queries[-1]
    assert all('JSONExtract' not in query for query in queries)


def test_eight_ticker_inspection_reads_one_exact_date_predecessor_batch():
    plan = selector().payload()['plan']
    names = [f'TEST{letter}' for letter in 'ABCDEFGH']
    plan['tickers'] = names
    plan['genesis_proofs'] = {name:{'prior_canonical_event_count':0,'canonical_absence_hash':digest(name+'absence')} for name in names}
    selected = CanonicalV7Selector(canonical_json(plan),digest(plan))
    records = []
    for name in names:
        initial_receipt = receipt(selected,'2026-08-24','empty');initial_receipt['ticker']=name
        initial = empty_or_carry_checkpoint(selected,initial_receipt)
        records.append(checkpoint_record(selected,initial_receipt,initial))
        carry_receipt = receipt(selected,'2026-08-25','carry',initial['checkpoint_hash']);carry_receipt['ticker']=name
        carry = empty_or_carry_checkpoint(selected,carry_receipt,predecessor=initial)
        records.append(checkpoint_record(selected,carry_receipt,carry,predecessor=initial))
    scope = SimpleNamespace(build_id='source-build',sessions=('2026-08-26',),units=tuple(
        SimpleNamespace(stage='bars',session_date='2026-08-26',ticker=name) for name in names))
    reader = Reader(selected,records)
    inspection = inspect_canonical_seed_plan(scope,reader,selected)
    assert len(inspection.units)==8 and not inspection.executable
    assert len([q for q in reader.queries if q.startswith('SELECT record_json')])==2
