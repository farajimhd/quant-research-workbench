from dataclasses import asdict
import copy

import pytest

from src.trading_runtime.declared_native_management_request import (
    DeclaredManagementRequestPolicy, inherited_fixed_management_request_policy,
    parse_declared_management_request, declared_management_intents,
)
from src.trading_runtime.execution_policies import (
    ExecutionPolicy, ExecutionPolicyName, ExecutionEnvelope, PartialFillPolicy,
)


def test_complete_inherited_exit_terms_and_roundtrip():
    policy = inherited_fixed_management_request_policy()
    value = policy.payload()
    original = ExecutionPolicy(policy_id="strategy-adaptive_urgent",
        name=ExecutionPolicyName.ADAPTIVE_URGENT,
        envelope=ExecutionEnvelope(persist_until_cancelled=True),
        partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source="qmd")
    assert value["exit"]["execution_policy"] == asdict(original)
    assert value["exit"]["time_in_force"] == "DAY"
    assert value["protection"]["time_in_force"] == ""
    assert value["protection"]["execution_policy"] is None
    assert parse_declared_management_request(value) == policy
    value["exit"]["execution_policy"]["envelope"]["deadline_ms"] = 1
    assert policy.payload()["exit"]["execution_policy"]["envelope"]["deadline_ms"] == 750


@pytest.mark.parametrize("path,value", [
    (("quantity_rule",), "cash_fraction"), (("capital_request",), {}),
    (("outside_rth_rule",), "always_true"), (("reference_price_rule",), "ask"),
    (("exit", "time_in_force"), ""), (("exit", "action"), "enter_long"),
    (("exit", "execution_policy", "revision"), True),
    (("exit", "execution_policy", "revision"), 1.0),
    (("exit", "execution_policy", "envelope", "deadline_ms"), 750.0),
    (("exit", "execution_policy", "envelope", "persist_until_cancelled"), 1),
    (("protection", "execution_policy"), {}),
    (("protection", "order_rule"), "stop_before_target"),
    (("protection", "stop_reasons"), ["arbitrary"]),
])
def test_request_drift_and_scalar_aliases_reject(path, value):
    payload = copy.deepcopy(inherited_fixed_management_request_policy().payload())
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValueError):
        parse_declared_management_request(payload)


@pytest.mark.parametrize("value", [None, {}, "{}", 1, True])
def test_policy_cannot_be_inferred(value):
    with pytest.raises(ValueError):
        DeclaredManagementRequestPolicy(value)


from test_backtest_declared_native_fixed_entry import parent
from test_backtest_declared_native_fixed_management import manager, held, rows


@pytest.fixture
def commands(manager):
    from dataclasses import replace
    from datetime import date
    from src.trading_runtime.declared_native_management_command import (
        DeclaredManagementContext, DeclaredExitInputs, DeclaredExitCommand, DeclaredSessionCommand,
    )
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
    m, _, _, _, source, view = manager
    view = replace(view, position_quantity=5.)
    context = DeclaredManagementContext(m.preparation.run_id, m.preparation.source.token,
        m.preparation, date.fromisoformat(m.preparation.source.parent.market.sessions[0]),
        source, view, m.policy, 40000, 35000, m._liquidity_sources[source.ticker])
    completed = FollowThroughFailureInput(40000, 35000, source.reference_ask, source.initial_stop,
        40000, round(source.initial_stop*10000), True, .01, .02,
        source.initial_stop, source.initial_stop+.0001, 0, 5., False)
    inputs = DeclaredExitInputs(completed, {}, ())
    kind, witness = inputs.replay(context)
    return DeclaredExitCommand(context, inputs, kind, witness), DeclaredSessionCommand


def test_real_completed_exit_replay_preserves_route_quantity_and_own_identity(commands):
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    command, _ = commands
    policy = inherited_fixed_management_request_policy()
    own, = declared_management_intents(command, policy)
    # Test-only existing request control; never submitted as this family's source.
    control = followthrough_exit_intent(command.witness, command.context.financial,
        session_date=command.context.session_date,
        source_entry_intent_id=command.context.source.intent_id, strategy_number=59)
    assert own.execution_policy == control.execution_policy
    assert own.quantity == control.quantity == 5.
    assert own.reference_price == control.reference_price
    assert own.time_in_force == control.time_in_force == 'DAY'
    assert own.outside_rth == control.outside_rth
    assert own.capital_request is None and own.protection_profile is None
    assert own.intent_id != control.intent_id
    assert own.reason == policy.payload()['exit']['reason_prefix'] + command.kind
    assert declared_management_intents(command, policy) == (own,)
    with pytest.raises(TypeError):
        own.metadata['source'] = command


def test_forged_selected_priority_rejects_before_intent(commands):
    from dataclasses import replace
    command, _ = commands
    with pytest.raises(ValueError):
        declared_management_intents(replace(command, kind='profit_giveback'),
                                    inherited_fixed_management_request_policy())


@pytest.mark.parametrize('mutation', ['valid', 'future', 'stale', 'crossed', 'foreign', 'missing', 'pending',
                                    'valid_bool', 'valid_float'])
def test_session_quote_missing_behavior_and_causal_freshness(commands, mutation):
    from dataclasses import replace
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.entry_spread_risk import exact_epoch_us
    command, Session = commands
    context = replace(command.context, boundary_ms=19740000)
    now = exact_epoch_us(market_day_boundary(context.session_date, context.boundary_ms))
    row = dict(ticker=context.financial.ticker, boundary_ms=context.boundary_ms,
        bid_int=10000, ask_int=10001, quote_valid=1, quote_timestamp_us=now)
    if mutation == 'future': row['quote_timestamp_us'] += 1
    elif mutation == 'stale': row['quote_timestamp_us'] -= 1000001
    elif mutation == 'crossed': row['ask_int'] = 9999
    elif mutation == 'foreign': row['ticker'] += 'OTHER'
    elif mutation == 'valid_bool': row['quote_valid'] = True
    elif mutation == 'valid_float': row['quote_valid'] = 1.0
    elif mutation == 'pending': context = replace(context, financial=replace(context.financial, pending_entry=True))
    session = Session(context, {} if mutation == 'missing' else {100: row})
    intents = declared_management_intents(session, inherited_fixed_management_request_policy())
    assert len(intents) == (1 if mutation == 'valid' else 0)
    if intents:
        assert intents[0].quantity == 5. and intents[0].reference_price == 1.


def test_mutated_policy_and_foreign_command_types_reject(commands):
    from dataclasses import replace
    command, _ = commands
    policy = inherited_fixed_management_request_policy()
    with pytest.raises(ValueError): declared_management_intents(object(), policy)
    with pytest.raises(ValueError): declared_management_intents(command, None)
    with pytest.raises(ValueError): replace(policy, declaration_json='{}')


def test_actual_manager_protection_command_projects_inherited_amendment_terms(manager):
    import asyncio
    from dataclasses import replace
    from src.trading_runtime.strategy_one_position import ResistanceBreak
    from src.trading_runtime.strategy_one_protection_intent import strategy_one_protection_intents

    async def run():
        m, runtime, evidence, _, source, _ = manager
        captured = []
        original = runtime.submit_declared_management
        async def retain(command):
            captured.append(command)
            return await original(command)
        runtime.submit_declared_management = retain
        view = await held(manager)
        breaks = tuple(ResistanceBreak(40000, dict(unified_level_id=f'R{index}',
            lower=source.reference_ask+index*.02, upper=source.reference_ask+index*.02+.001,
            role='resistance', side='resistance')) for index in (1, 2, 3))
        evidence.rows[40000], frame = rows(m, source, 40000, bid=source.reference_ask+.2, breaks=breaks)
        await m.on_management(view, frame, 40000)
        command, = captured
        projected = declared_management_intents(command, inherited_fixed_management_request_policy())
        control = strategy_one_protection_intents(command.inputs.previous, command.transition,
            command.context.financial, session_date=command.context.session_date,
            bid=command.inputs.bid, ask=command.inputs.ask, strategy_number=59)
        assert len(projected) == len(control) == 1
        own, inherited = projected[0], control[0]
        assert own.action == inherited.action == 'replace_protective_stop'
        assert own.invalidation_price == inherited.invalidation_price
        assert own.execution_policy is inherited.execution_policy is None
        assert own.time_in_force == inherited.time_in_force == ''
        assert own.quantity == inherited.quantity
        assert own.reference_price == inherited.reference_price
        assert own.resolved_execution_policy() == inherited.resolved_execution_policy()
        assert own.intent_id != inherited.intent_id
        bad = replace(command, transition=replace(command.transition,
            state=replace(command.transition.state, stop=command.transition.state.stop+.001)))
        with pytest.raises(ValueError):
            declared_management_intents(bad, inherited_fixed_management_request_policy())
    asyncio.run(run())
