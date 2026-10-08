"""Prepared release semantics; not installed execution or financial evidence."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest
from types import SimpleNamespace

from src.backend.backtest_fixed_structural_lot_compatibility_v13 import (
    REVIEWED_PARENT_DELTAS, restore_reviewed_parent_source,
)
from src.trading_runtime.decimal_snapshot_readback import RULE
from src.trading_runtime.strategy_ninety_release import release_contract as parent_release
from src.trading_runtime.strategy_ninety_two_release import release_contract as successor_release
from src.trading_runtime.strategy_ninety_contract import strategy_ninety_contract
from src.trading_runtime.strategy_ninety_two_contract import strategy_ninety_two_contract


def test_successor_changes_only_declared_decimal_readback_semantics():
    parent, successor = parent_release(), successor_release()
    parent.verify()
    successor.verify()
    assert successor.number == successor.executor_revision == 92
    assert successor.rule_set_contracts == (*parent.rule_set_contracts, RULE)
    assert successor.input_contracts == parent.input_contracts
    assert successor.executor_strategy_id == parent.executor_strategy_id
    assert successor.evaluation_interval == parent.evaluation_interval
    assert successor.digest() != parent.digest()


def test_successor_retains_complete_trading_and_lot_policies():
    parent, successor = strategy_ninety_contract(), strategy_ninety_two_contract()
    assert successor.policy_json == parent.policy_json
    assert successor.fixed_structural_lot_policy == parent.fixed_structural_lot_policy


@pytest.mark.parametrize('relative', tuple(REVIEWED_PARENT_DELTAS))
def test_complete_parent_restore_rejects_unreviewed_source(relative):
    root = Path(__file__).resolve().parents[1]
    source = (root / relative).read_text(encoding='utf-8')
    _, baseline, _ = REVIEWED_PARENT_DELTAS[relative]
    restored = restore_reviewed_parent_source(source, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == baseline
    foreign = source + '\nforeign_unreviewed_delta = True\n'
    assert restore_reviewed_parent_source(foreign, relative) == foreign


def test_old_release_delegates_to_unchanged_execution_route(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_execution_v13 as successor
    sentinel = object()
    observed = []
    monkeypatch.setattr(successor.legacy, 'prepare_fixed_structural_lot_session',
                        lambda **values: observed.append(values) or sentinel)
    arguments = dict(plans=object(), number=90, run_id='controlled', session_date=None,
                     market=object(), candidates=object(), entry=object(), seeds=object(),
                     through_boundary_ms=0, client_factory=None)
    assert successor.prepare_fixed_structural_lot_session(**arguments) is sentinel
    assert observed == [arguments]


def test_decimal_release_requires_exact_whole_plan_before_source_reads():
    from src.backend.backtest_fixed_structural_lot_execution_v13 import prepare_fixed_structural_lot_session
    plans = SimpleNamespace(market=object(), candidates=object(), entry=object(), seeds=object())
    with pytest.raises(ValueError, match='exact whole certified plans'):
        prepare_fixed_structural_lot_session(
            plans=plans, number=92, run_id='controlled', session_date=None,
            market=object(), candidates=plans.candidates, entry=plans.entry,
            seeds=plans.seeds, through_boundary_ms=0, client_factory=None,
        )


def test_real_issued_successor_source_selects_decimal_readback(monkeypatch):
    """Real native issuance/certification; market/config transports and Git identity controlled.

    This is an explicit proposed-source integration fixture, never saved P&L
    or evidence of an actual installed app revision.
    """
    import subprocess
    from tests.test_fixed_structural_lot_checkpoint_reader_profile import selected
    from src.trading_runtime.decimal_snapshot_readback import declared_decimal_rows
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
    original = subprocess.check_output
    monkeypatch.setattr(subprocess, 'check_output', lambda args, **kwargs:
                        (commit + '\n').encode() if args == ['git', 'rev-parse', 'HEAD'] else
                        b'' if args == ['git', 'status', '--porcelain'] else
                        original(args, **kwargs))
    prepared, _, _ = selected(monkeypatch, actual_loader=True, number=92, version=13)
    source = prepared.operation.source
    table = SimpleNamespace(columns=(('price', 'Decimal(38, 18)'),))
    assert declared_decimal_rows(source, table, ({'price': '5.3'},)) == (
        {'price': '5.300000000000000000'},)
    with pytest.raises(ValueError):
        declared_decimal_rows(source, table, ({'price': 5.3},))

