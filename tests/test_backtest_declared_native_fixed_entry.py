"""Real typed producer fixtures; prepared-only, not native installation proof."""
from dataclasses import replace
from datetime import date
import json
import re
from types import SimpleNamespace

import numpy as np
import pytest

from test_declared_native_fixed_capabilities import prepared
from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from test_backtest_strategy_first_price_source import Bars
from src.backend.backtest_declared_native_fixed_plan import (
    compile_declared_momentum_plan, load_declared_entry_source_plan,
)
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation, ENTRY_FAMILY
from src.backend.backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
from src.trading_runtime.entry_spread_risk import exact_epoch_us, EntrySpreadRiskPolicy
from src.trading_runtime.declared_native_fixed_candidate import NativeFixedCandidateSpec, NativeFixedPolicyDelta, CandidateLabels, SPREAD_RULE
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.backend.backtest_strategy_rising_momentum import _seal
from src.trading_runtime.entry_momentum_growth import declared_initial_entry

RUN = '12345678-1234-4234-8234-123456789abc'


@pytest.fixture
def parent(monkeypatch):
    capabilities = prepared.__wrapped__(SimpleNamespace(param=59), monkeypatch)[0]
    market, price = source_authority()
    initial = price.source.parent
    broker = replace(market.units[0], stage='broker_100ms', attempt_id=initial.candidates.coverage[0].source_attempts[2])
    market = replace(market, units=(*market.units, broker))
    return compile_declared_momentum_plan(capabilities, market, initial.candidates, initial.entry, initial.momentum)


class Source:
    def __init__(self, parent, *, price='normal', activity='normal', quote='normal', spread=1):
        self.parent, self.price, self.activity = parent, Bars(price), ActivityBars(activity)
        self.quote, self.spread, self.queries = quote, spread, []

    def iter_arrow_record_batches(self, sql):
        self.queries.append(sql)
        return (self.price if 'resolution_ms=1000 ' in sql else self.activity).iter_arrow_record_batches(sql)

    def execute(self, sql):
        self.queries.append(sql)
        scope = re.search(r'IN \((.*)\) SETTINGS', sql).group(1)
        rows = []
        for ticker, bucket, attempt in re.findall(r"\('([^']+)',(\d+),toUUID\('([^']+)'\)\)", scope):
            boundary = (int(bucket)+1)*100-SESSION_OPEN_OFFSET_MS
            fact = self.parent.entry.lookup(ticker, boundary)
            # Real structural stop/target fixture; independent canonical quote.
            ask = round((fact.stop_price + .02)*10000)
            at = exact_epoch_us(market_day_boundary(date.fromisoformat(self.parent.market.sessions[0]), boundary))
            rows.append(dict(ticker=ticker, bucket_index=int(bucket), liquidity_attempt_id=attempt,
                             bid_int=ask-self.spread, ask_int=ask, quote_timestamp_us=at,
                             quote_valid=1))
        if self.quote == 'missing': rows = rows[:-1]
        if self.quote == 'duplicate': rows += rows[:1]
        if self.quote == 'foreign': rows[0]['liquidity_attempt_id'] = '22222222-2222-4222-8222-222222222222'
        if self.quote == 'future': rows[0]['quote_timestamp_us'] += 1
        if self.quote == 'bool': rows[0]['bid_int'] = True
        if self.quote == 'stale': rows[0]['quote_timestamp_us'] -= 1000001
        return '\n'.join(json.dumps(row) for row in rows)


def financial(parent):
    return StrategyOneFinancialView('assignment', 'account', parent.momentum.keys[0][0],
        AssignmentStatus.WATCHING, StrategyPermissions(observe=True, enter=True),
        0., False, False, False, 0)


def test_full_causal_chain_keeps_anchor_and_proposes_exact_own_identity(parent):
    assert parent.first_indices.tolist() == [0, 0]
    source = load_declared_entry_source_plan(parent, client=Source(parent))
    assert source.eligible_mask.tolist() == [True, True]
    prep = DeclaredEntryPreparation(RUN, 'assignment', 'account', source)
    key = parent.momentum.keys[0]
    decision = prep.propose(*key, financial(parent))
    assert decision.reason == 'entry_proposed'
    proposal = decision.proposal
    assert proposal.strategy_number == parent.capabilities.identity.strategy_number
    assert proposal.strategy_number != parent.capabilities.parent.strategy_number
    assert proposal.family == ENTRY_FAMILY and proposal.run_id == RUN
    assert proposal.source_token == source.token
    assert prep.verify_proposal(proposal, financial(parent)) == proposal
    assert prep.propose(*key, financial(parent)).proposal == proposal
    for changed in (replace(proposal, run_id='22222222-2222-4222-8222-222222222222'),
                    replace(proposal, strategy_number=parent.capabilities.parent.strategy_number),
                    replace(proposal, source_token='f'*64), replace(proposal, intent_id=RUN)):
        with pytest.raises(ValueError, match='differs'):
            prep.verify_proposal(changed, financial(parent))
    with pytest.raises(ValueError): source.eligible_mask.setflags(write=True)


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'foreign', 'future', 'bool'])
def test_quote_exact_coverage_clock_and_types(parent, mode):
    with pytest.raises(ValueError):
        load_declared_entry_source_plan(parent, client=Source(parent, quote=mode))


@pytest.mark.parametrize('mode', ['duplicate', 'outside', 'flags'])
def test_price_source_corruption_rejected(parent, mode):
    with pytest.raises(ValueError): load_declared_entry_source_plan(parent, client=Source(parent, price=mode))


@pytest.mark.parametrize('mode', ['duplicate', 'outside', 'wrong_resolution'])
def test_activity_source_corruption_rejected(parent, mode):
    with pytest.raises(ValueError): load_declared_entry_source_plan(parent, client=Source(parent, activity=mode))


def test_missing_original_price_never_promotes_later_candidate(parent):
    source = load_declared_entry_source_plan(parent, client=Source(parent, price='missing'))
    assert not source.eligible_mask.any()
    assert source.parent.first_indices.tolist() == [0, 0]


def test_confirmed_fade_vetoes_rest_of_original_episode(parent):
    source = load_declared_entry_source_plan(parent, client=Source(parent, activity='fade'))
    assert not source.eligible_mask.any()
    missing = load_declared_entry_source_plan(parent, client=Source(parent, activity='missing'))
    assert not missing.eligible_mask.any()


def test_source_and_scope_mutations_fail_closed(parent):
    with pytest.raises(ValueError): replace(parent, token='f'*64)
    with pytest.raises(ValueError): replace(parent, eligible_mask=np.array([False, True]))
    source = load_declared_entry_source_plan(parent, client=Source(parent))
    with pytest.raises(ValueError): replace(source, token='f'*64)
    columns = tuple(a.copy() for a in source.quote_columns)
    columns[1][0] += 1
    with pytest.raises(ValueError): replace(source, quote_columns=columns)
    prep = DeclaredEntryPreparation(RUN, 'assignment', 'account', source)
    with pytest.raises(ValueError): prep.propose(*parent.momentum.keys[0], replace(financial(parent), account_id='foreign'))


@pytest.mark.parametrize('change,reason', [
    ({'pending_entry':True}, 'entry_fill_pending'),
    ({'position_quantity':1.}, 'position_requires_management'),
    ({'permissions':StrategyPermissions(observe=True, enter=False)}, 'entry_permission_closed'),
    ({'completed_entries':1, 'permissions':StrategyPermissions(observe=True, enter=True, reenter=True)}, 'reentry_structure_confirmation_unavailable'),
])
def test_shared_financial_admission_remains_sequential(parent, change, reason):
    source = load_declared_entry_source_plan(parent, client=Source(parent))
    prep = DeclaredEntryPreparation(RUN, 'assignment', 'account', source)
    assert prep.propose(*parent.momentum.keys[0], replace(financial(parent), **change)).reason == reason


def test_declared_delta_only_not_free_policy_flag(parent):
    candidate = NativeFixedCandidateSpec(parent.capabilities,
        NativeFixedPolicyDelta(spread=EntrySpreadRiskPolicy(SPREAD_RULE,(1,4))),
        CandidateLabels('Prepared fixture','fixture','fixture','Prepared only'))
    broad = load_declared_entry_source_plan(parent, client=Source(parent, spread=100))
    narrow = load_declared_entry_source_plan(parent, client=Source(parent, spread=100), candidate=candidate)
    assert broad.eligible_mask.any() and not narrow.eligible_mask.any()
    assert narrow.token != broad.token
    with pytest.raises(ValueError): load_declared_entry_source_plan(parent, client=Source(parent), candidate=object())


def test_stale_quote_rejects_current_without_resetting_first(parent):
    source = load_declared_entry_source_plan(parent, client=Source(parent, quote='stale'))
    assert source.eligible_mask.tolist() == [True, True]
    prep = DeclaredEntryPreparation(RUN, 'assignment', 'account', source)
    assert prep.propose(*parent.momentum.keys[0], financial(parent)).proposal is None
    assert source.parent.first_indices.tolist() == [0,0]


def changed_momentum(parent, *, keys=None, arrays=None, attempts=None):
    old = parent.momentum
    arrays = arrays or tuple(getattr(old, n).copy() for n in (
        'current_boundaries_ms','prior_boundaries_ms','current_line','current_signal','prior_line','prior_signal'))
    keys, attempts = keys or old.keys, attempts or old.source_attempts
    requested = np.ones(len(keys), dtype=np.bool_)
    token = _seal(old.source_build_id, parent.market.token, parent.candidates.token,
                  keys, attempts, (requested,*arrays))
    return replace(old, keys=keys, source_attempts=attempts, requested_mask=requested,
                   current_boundaries_ms=arrays[0], prior_boundaries_ms=arrays[1],
                   current_line=arrays[2], current_signal=arrays[3], prior_line=arrays[4],
                   prior_signal=arrays[5], token=token)


def test_two_tickers_repeated_clocks_and_original_episode_parity(parent):
    candidate = replace(parent.candidates,
        prepared=(*parent.candidates.prepared,replace(parent.candidates.prepared[0],ticker='ZZZ')),
        coverage=(*parent.candidates.coverage,replace(parent.candidates.coverage[0],ticker='ZZZ')))
    entry = replace(parent.entry,
        candidates=(*parent.entry.candidates,*(replace(f,ticker='ZZZ') for f in parent.entry.candidates)),
        activations=(*parent.entry.activations,*(replace(a,ticker='ZZZ') for a in parent.entry.activations)))
    market = replace(parent.market, units=(*parent.market.units,*(replace(u,ticker='ZZZ') for u in parent.market.units)))
    arrays = tuple(np.concatenate((a,a)) for a in (parent.momentum.current_boundaries_ms,
        parent.momentum.prior_boundaries_ms,parent.momentum.current_line,parent.momentum.current_signal,
        parent.momentum.prior_line,parent.momentum.prior_signal))
    momentum = changed_momentum(parent,keys=(*parent.momentum.keys,('ZZZ',31000),('ZZZ',41000)),arrays=arrays,attempts=parent.momentum.source_attempts*2)
    scope = compile_declared_momentum_plan(parent.capabilities,market,candidate,entry,momentum)
    source = load_declared_entry_source_plan(scope,client=Source(scope))
    assert scope.first_indices.tolist() == [0,0,2,2]
    assert source.eligible_mask.tolist() == [True]*4
    assert load_declared_entry_source_plan(scope,client=Source(scope,activity='fade')).eligible_mask.tolist() == [False]*4


def test_missing_first_momentum_does_not_shift_anchor_and_scalar_matches_vector(parent):
    arrays = tuple(getattr(parent.momentum,n).copy() for n in ('current_boundaries_ms','prior_boundaries_ms','current_line','current_signal','prior_line','prior_signal'))
    arrays[2][0,1] = np.nan
    missing = compile_declared_momentum_plan(parent.capabilities,parent.market,parent.candidates,parent.entry,changed_momentum(parent,arrays=arrays))
    assert missing.first_indices.tolist() == [0,0]
    assert missing.eligible_mask.tolist() == [False,False]
    from src.backend.backtest_declared_native_fixed_plan import _policy
    for plan in (parent,missing):
        assert [declared_initial_entry(plan.momentum.lookup(*key),plan.initial(i),_policy(plan.capabilities))
                for i,key in enumerate(plan.momentum.keys)] == plan.eligible_mask.tolist()


def test_exact_policy_schema_rejects_numeric_alias(parent):
    inherited = parent.capabilities.payload()['inherited']
    inherited['policies']['entry_activity_policy']['resolution_ms'] = 5000.0
    caps = replace(parent.capabilities,inherited_json=json.dumps(inherited,sort_keys=True,separators=(',',':')))
    with pytest.raises(ValueError,match='unsupported'):
        compile_declared_momentum_plan(caps,parent.market,parent.candidates,parent.entry,parent.momentum)


def test_intent_scope_uses_unambiguous_serialization(parent):
    source = load_declared_entry_source_plan(parent,client=Source(parent))
    ids=[]
    for assignment,account in (('a:b','c'),('a','b:c')):
        prep=DeclaredEntryPreparation(RUN,assignment,account,source)
        ids.append(prep.propose(*parent.momentum.keys[0],replace(financial(parent),assignment_id=assignment,account_id=account)).proposal.intent_id)
    assert ids[0] != ids[1]


@pytest.mark.parametrize('offset,eligible', [(43200000,True),(19800000,False)])
def test_causal_afterhours_and_regular_hours_keep_inherited_window(parent,offset,eligible):
    row=parent.candidates.prepared[0]
    candidate=replace(parent.candidates,prepared=(replace(row,
        boundary_ms=row.boundary_ms+offset,episode_start_ms=row.episode_start_ms+offset,
        macd_boundary_ms=row.macd_boundary_ms+offset,
        stop_bar_boundary_ms=row.stop_bar_boundary_ms+offset),))
    entry=replace(parent.entry,
        candidates=tuple(replace(f,boundary_ms=f.boundary_ms+offset,
                         episode_start_ms=f.episode_start_ms+offset,
                         bos_break_boundary_ms=f.bos_break_boundary_ms+offset) for f in parent.entry.candidates),
        activations=tuple(replace(a,episode_start_ms=a.episode_start_ms+offset) for a in parent.entry.activations))
    arrays=tuple(getattr(parent.momentum,n).copy() for n in ('current_boundaries_ms','prior_boundaries_ms','current_line','current_signal','prior_line','prior_signal'))
    arrays[0][:]+=offset;arrays[1][:]+=offset
    momentum=changed_momentum(parent,keys=tuple((ticker,b+offset) for ticker,b in parent.momentum.keys),arrays=arrays)
    scope=compile_declared_momentum_plan(parent.capabilities,parent.market,candidate,entry,momentum)
    sourceclient=Source(scope)
    source=load_declared_entry_source_plan(scope,client=sourceclient)
    assert source.eligible_mask.tolist()==[eligible,eligible]
    assert not source.price_columns[0].any() # AH never borrows PM price evidence.
    if eligible:
        prep=DeclaredEntryPreparation(RUN,'assignment','account',source)
        assert prep.propose(*scope.momentum.keys[0],financial(scope)).proposal is not None
    else:
        assert not sourceclient.queries # Closed RTH performs no financial-source reads.


def test_technical_attempt_and_source_identity_drift_rejected(parent):
    market=replace(parent.market,units=tuple(replace(u,attempt_id='22222222-2222-4222-8222-222222222222')
        if u.stage=='technical' else u for u in parent.market.units))
    with pytest.raises(ValueError,match='attempt differs'):
        compile_declared_momentum_plan(parent.capabilities,market,parent.candidates,parent.entry,parent.momentum)
    with pytest.raises(ValueError,match='identity'):
        compile_declared_momentum_plan(parent.capabilities,replace(parent.market,build_id='f'*64),parent.candidates,parent.entry,parent.momentum)


def test_broker_attempt_mismatch_rejected_before_any_sql(parent):
    market=replace(parent.market,units=tuple(replace(u,attempt_id='22222222-2222-4222-8222-222222222222')
        if u.stage=='broker_100ms' else u for u in parent.market.units))
    scoped=compile_declared_momentum_plan(parent.capabilities,market,parent.candidates,parent.entry,parent.momentum)
    client=Source(scoped)
    with pytest.raises(ValueError,match='attempt differs'):
        load_declared_entry_source_plan(scoped,client=client)
    assert client.queries == []
    assert client.price.queries == []
    assert client.activity.queries == []


@pytest.mark.parametrize('field,value',[('boundary_ms',31000.0),('episode_start_ms',True),('reference_ask',1),('revision',True)])
def test_own_proposal_rejects_scalar_type_aliases(parent,field,value):
    source=load_declared_entry_source_plan(parent,client=Source(parent))
    proposal=DeclaredEntryPreparation(RUN,'assignment','account',source).propose(*parent.momentum.keys[0],financial(parent)).proposal
    with pytest.raises(ValueError,match='typed shape'):
        replace(proposal,**{field:value})
