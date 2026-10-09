"""Immutable original-authority isolation successor preserves full trading behavior."""
from copy import deepcopy
from dataclasses import fields
import pytest

from test_fixed_structural_lot_configuration_routing import source_fixture
from test_strategy_fifty_release import APPROVAL
from src.trading_runtime.strategy_one_hundred_three_contract import strategy_one_hundred_three_contract
from src.trading_runtime.strategy_one_hundred_four_contract import strategy_one_hundred_four_contract
from src.trading_runtime.strategy_one_hundred_three_release import derive_strategy_one_hundred_three_configuration
from src.trading_runtime.strategy_one_hundred_four_release import (
    derive_strategy_one_hundred_four_configuration,
    verify_prepared_strategy_one_hundred_four_configuration,
)
from src.trading_runtime.fixed_lot_management_native_preparation import uses_management_native_preparation
from src.trading_runtime.declared_native_manifest import registered_manifest_authority


def test_complete_predecessor_tree_and_trading_factory_are_preserved():
    parent = source_fixture()
    prior = derive_strategy_one_hundred_three_configuration(parent, **APPROVAL)
    current = derive_strategy_one_hundred_four_configuration(parent, **APPROVAL)
    restored = deepcopy(current['payload'])
    for section, keys in {
        'strategy': ('strategy_number', 'revision', 'profile_id', 'profile_revision', 'name', 'numbered_release'),
        'strategy_profile': ('profile_id', 'revision', 'definition_revision', 'name', 'description'),
        'run_plan': ('profile_id', 'name', 'description'),
    }.items():
        for key in keys:
            restored[section][key] = deepcopy(prior['payload'][section][key])
    assert restored == prior['payload']
    previous, own = strategy_one_hundred_three_contract(), strategy_one_hundred_four_contract()
    for field in fields(previous):
        if field.name not in ('strategy_number', 'release'):
            assert getattr(own, field.name) == getattr(previous, field.name)
    assert uses_management_native_preparation(own.release)
    assert verify_prepared_strategy_one_hundred_four_configuration(parent, current['payload']) == current
    changed = deepcopy(current['payload'])
    changed['strategy']['parameters']['execution']['tick_size'] = .02
    with pytest.raises(ValueError, match='complete inherited compiler tree'):
        verify_prepared_strategy_one_hundred_four_configuration(parent, changed)


def test_registered_successor_full_source_authority():
    authority = registered_manifest_authority(104)
    assert authority.parent_number == 42
    fingerprint = authority.certify_source()
    assert len(fingerprint) == 64


from dataclasses import replace
from test_fixed_lot_management_native_preparation import (
    exact_parent, current_approval, certified, controlled_clean_status,
)

def prepared_nonempty_session(monkeypatch, *, resume=False):
    number, version = (104, 20)
    from test_fixed_structural_lot_checkpoint_reader_profile import ordinal_transport_plan, controlled_price_rows
    from importlib import import_module
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    from test_fixed_structural_lot_source_v2 import inputs
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    source = import_module(f'src.backend.backtest_fixed_structural_lot_source_v{version}')
    from src.backend import backtest_fixed_structural_lot_source_v5 as scope_owner
    native = import_module(f'src.backend.backtest_fixed_structural_lot_native_v{version}')
    from src.backend import backtest_fixed_structural_lot_native as owner
    session = import_module(f'src.backend.backtest_fixed_structural_lot_execution_v{version}')
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans, authority, old, proposal, calls = inputs(monkeypatch)
    authority = replace(authority, entry_activity_source=replace(authority.entry_activity_source, strategy_number=number))
    plans = replace(plans, v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan', 'certify_candidate_plan', 'certified_seed_plan', '_load_quotes'):
        monkeypatch.setattr(source if name == '_load_quotes' else scope_owner, name, getattr(previous, name))
    monkeypatch.setattr(scope_owner, 'certify_v7_interval_plan', lambda *a, **kw: plans.v7_intervals)
    parent = exact_parent()
    own = certified(derive_strategy_one_hundred_four_configuration(parent, **current_approval()))
    from src.backend import backtest_fixed_structural_lot_native as source_issuer
    controlled_clean_status(monkeypatch)
    monkeypatch.setattr(source, 'certify_numbered_configuration', lambda client, selected: parent if selected == 42 else own)
    monkeypatch.setattr(native, 'certify_numbered_configuration', lambda client, selected: own if selected == number else parent)
    complete_selected = True
    monkeypatch.setattr(execution, 'prepare_strategy_one_entry_authorities', lambda **kw: (plans.candidates, authority.entry_activity_source.gate, None, None, None, (), authority))

    class Client:

        def close(self):
            calls.append(('closed',))

        def execute(self, query):
            if not complete_selected:
                raise AssertionError('Legacy fixture unexpectedly queried price transport')
            return controlled_price_rows(plans, query)
    if complete_selected:
        from src.backend.backtest_liquidity_price import certify_price_level_plan
        plans = replace(plans, prices=certify_price_level_plan(plans.execution_market, Client()))
    if resume:
        import ast
        import asyncio
        from pathlib import Path
        from types import SimpleNamespace
        from src.backend import backtest_market_data
        tree = ast.parse((Path(__file__).parents[1] / 'src/backend/replay_run_service.py').read_text(encoding='utf-8'))
        blocks = [node for node in ast.walk(tree) if isinstance(node, ast.If) and any((isinstance(child, ast.ImportFrom) and child.module == 'backtest_fixed_structural_lot_execution_v20' for child in node.body))]
        assert len(blocks) == 2
        block = max(blocks, key=lambda node: node.lineno)
        wrapper = ast.AsyncFunctionDef(name='resume_preparation', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[block, ast.Return(value=ast.Name(id='selected_lot_session', ctx=ast.Load()))], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        monkeypatch.setattr(backtest_market_data, 'readonly_clickhouse_client', lambda **kwargs: Client())
        from src.backend.backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract
        namespace = dict(__package__='src.backend', asyncio=asyncio, declared_fixed_structural_lot_contract=declared_fixed_structural_lot_contract, strategy_number=number, run_id=old.run_id, plans=plans, definition=SimpleNamespace(session_date=old.session_date), controller=SimpleNamespace(_fixed_through_boundary_ms=lambda: 57600000))
        exec(compile(module, '<actual replay resume preparation block>', 'exec'), namespace)
        actual = asyncio.run(namespace['resume_preparation']())
    else:
        actual = session.prepare_fixed_structural_lot_session(plans=plans, number=number, run_id=old.run_id, session_date=old.session_date, market=plans.market, candidates=plans.candidates, entry=plans.entry, seeds=plans.seeds, through_boundary_ms=57600000, client_factory=Client)
    actual.require(market=plans.market, candidates=plans.candidates, entry=plans.entry, through_boundary_ms=57600000, run_id=old.run_id, number=number)
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
    original = replace(_proposal(), strategy_number=18, boundary_ms=41000, target_level_id='R3', bos_support_level_id='R3', momentum=authority.plan.momentum.lookup('AAA', 41000), initial_momentum=authority.plan.source.parent.selection_witness('AAA', 41000))
    proposal = bind_episode_activity_proposal(authority, bind_certified_price_break_proposal(authority.plan, original, strategy_number=36), session_date=old.session_date)
    request = actual.operation.request(proposal)
    request.verify()
    return (actual, request, plans)

def test_real_installed_nonempty_successor_preparation(monkeypatch):
    actual, request, plans = prepared_nonempty_session(monkeypatch)
    assert actual.operation.source.installed_payload['strategy']['strategy_number'] == 104
    assert actual.operation.source.policy == strategy_one_hundred_four_contract().fixed_structural_lot_policy
    assert request.revision == 104
    request.verify()
