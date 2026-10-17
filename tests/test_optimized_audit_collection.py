import asyncio
from dataclasses import replace

import polars as pl
import pytest

from src.backend import optimized_audit_collection as audit
from src.trading_runtime.rule_decision_basis_projection import _seal
from src.trading_runtime.rule_decision_basis_schema import BASIS
from tests.test_recorded_program_comparison import inputs, install_reads, run_kwargs
from src.backend import recorded_program_comparison as comparison


def fixture(monkeypatch):
    prefix, packet, block, source, reference, decisions, evidence = inputs(monkeypatch)
    mask = block.masks.with_columns(*[pl.lit(False).alias(name)
        for stage in packet.actor.program_release.programs for name in (stage, stage + '__valid')])
    block = replace(block, masks=mask)
    adjusted = []
    for index, (parent, children, observation) in enumerate(decisions):
        parent = _seal(BASIS, {**{key: value for key, value in parent.items() if key != 'content_hash'}, 'basis_sequence': 18 + index * 10,
            'decision_sequence': 19 + index * 10})
        adjusted.append((parent, children, observation))
    evidence['fills'] = tuple(dict(row, sequence=20 + index * 10) for index, row in enumerate(evidence['fills']))
    evidence['commissions'] = tuple(dict(row, sequence=21 + index * 10) for index, row in enumerate(evidence['commissions']))
    return prefix, packet, block, source, reference, tuple(adjusted), evidence


def test_native_projection_replays_financial_sequence_and_keeps_oms_facts(monkeypatch):
    prefix, packet, block, source, _, decisions, evidence = fixture(monkeypatch)
    result = audit.project_native_execution_audit(prefix=prefix, packet=packet, blocks=(block,),
        source=source, financial=evidence, decisions=decisions, recorded_metrics='verified-native', maximum_rows=100)
    assert result['complete'] is True
    assert result['fills'].height == 4 and result['fees'].height == 4
    assert result['pre_decision_positions']['quantity'].to_list() == ['0', '10', '15', '10', '0']
    assert result['position_transitions']['after_quantity'].to_list() == ['10', '15', '10', '0']
    assert result['observed_orders']['broker_order_id'].unique().to_list() == ['stop']
    assert result['decisions'].height == 5
    assert 'not reconstructed' in result['unavailable']['membership']
    with pytest.raises(ValueError, match='row bound'):
        audit.project_native_execution_audit(prefix=prefix, packet=packet, blocks=(block,), source=source,
            financial=evidence, decisions=decisions, recorded_metrics='verified-native', maximum_rows=1)


def test_runnable_native_collector_joins_existing_verified_path(monkeypatch):
    prefix, packet, block, source, reference, decisions, evidence = fixture(monkeypatch)
    factory, clients, holder, _, _ = install_reads(monkeypatch, decisions, evidence)
    holder[0] = prefix
    # Existing recorded comparison performs real typed actor/source/window and
    # financial projection; only its read transport seams are fixture-backed.
    calls = dict(financial=0, observations=0)
    financial_read = comparison.load_typed_backtest_financial_evidence
    observation_read = comparison.read_rule_decision_observation_page_async
    def financial(*args, **kwargs):
        calls['financial'] += 1
        return financial_read(*args, **kwargs)
    async def observations(*args, **kwargs):
        calls['observations'] += 1
        return await observation_read(*args, **kwargs)
    monkeypatch.setattr(comparison, 'load_typed_backtest_financial_evidence', financial)
    monkeypatch.setattr(comparison, 'read_rule_decision_observation_page_async', observations)
    shared = []
    read_session = audit._read_recorded_program_session
    async def session(*args, **kwargs):
        result = await read_session(*args, **kwargs)
        shared.append(result)
        return result
    project = audit.project_native_execution_audit
    def projection(**kwargs):
        metrics, financial, observations = shared[0]
        assert kwargs['recorded_metrics'] is metrics
        assert kwargs['financial'] is financial is evidence
        assert kwargs['decisions'] is observations
        assert all(actual is expected for actual, expected in zip(observations, decisions, strict=True))
        return project(**kwargs)
    monkeypatch.setattr(audit, '_read_recorded_program_session', session)
    monkeypatch.setattr(audit, 'project_native_execution_audit', projection)
    result = asyncio.run(audit.collect_native_execution_audit(factory, prefix, packet, (block,), source,
        reference, maximum_audit_rows=100, **run_kwargs()))
    assert result['recorded_metrics'].app['metrics']['net_pnl'] == -19
    assert result['pre_decision_positions'].height == 5
    assert all(client.closed for client in clients) and len(clients) == 4
    assert len(shared) == 1 and calls == dict(financial=1, observations=3)



def test_native_actions_are_exact_recovered_intents_and_float_projection_explicit(monkeypatch):
    from uuid import uuid4
    from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.signals import StrategyIntent
    prefix, packet, block, source, _, decisions, evidence = fixture(monkeypatch)
    parent, _, observation = decisions[0]
    identity = str(uuid4())
    values = {name: value for name, value in parent.items() if name not in {'content_hash', 'intent_count', 'intent_hash'}}
    values.update(basis_sequence=17, selection_status='selected')
    parent, children = project_rule_decision_basis(values, ({'stage': 'entry', 'strategy_intent_id': identity},))
    intent = StrategyIntent(identity, 'ABC', observation.observed_at, 'enter_long', 1., 10., reason='program:entry')
    record = RecoveredIntent(18, 'account', str(uuid4()), parent['batch_id'], intent)
    decisions = ((parent, children, observation), *decisions[1:])
    result = audit.project_native_execution_audit(prefix=prefix, packet=packet, blocks=(block,), source=source,
        financial=evidence, decisions=decisions, recorded_metrics='verified-native', maximum_rows=100,
        intent_records={identity: record})
    assert result['intent_actions']['action'].to_list() == ['enter_long']
    assert result['intent_actions']['intent_id'].to_list() == [identity]
    assert result['pre_decision_positions']['quantity__float64'].to_list() == [0., 10., 15., 10., 0.]
    assert result['decisions']['state_identity_complete'].to_list() == [True] * 5
    assert result['pre_decision_positions'].schema['quantity'] == pl.String

