"""Real release routing with controlled preparation transport, not certification."""
from dataclasses import replace
from datetime import date

import pytest

from src.backend import backtest_declared_fixed_lot_saved_preparation as saved
from src.backend import backtest_fixed_structural_lot_execution_v13 as legacy
from src.backend import backtest_fixed_structural_lot_execution_v20 as selected
from src.trading_runtime.fixed_lot_management_native_preparation import INPUT, RULE
from src.trading_runtime.strategy_one_hundred_nine_release import release_contract


def arguments():
    return dict(plans=object(), number=release_contract().number, run_id='test-transport-only',
        session_date=date(2026, 8, 4), market=object(), candidates=object(),
        entry=object(), seeds=object(), through_boundary_ms=100,
        client_factory=lambda: None)


@pytest.mark.parametrize('declared', [True, False])
def test_complete_arguments_follow_semantic_declaration(monkeypatch, declared):
    release = release_contract()
    if not declared:
        draft = replace(release,
            input_contracts=tuple(v for v in release.input_contracts if v != INPUT),
            rule_set_contracts=tuple(v for v in release.rule_set_contracts if v != RULE),
            approved_digest='')
        release = replace(draft, approved_digest=draft.digest())
    release.verify()
    monkeypatch.setattr(saved, 'numbered_strategy', lambda number: release)
    result = object()
    calls = []
    target = selected if declared else legacy
    other = legacy if declared else selected
    monkeypatch.setattr(target, 'prepare_fixed_structural_lot_session',
        lambda **kwargs: calls.append(kwargs) or result)
    monkeypatch.setattr(other, 'prepare_fixed_structural_lot_session',
        lambda **kwargs: pytest.fail('Wrong declared preparation route'))
    kwargs = arguments()
    assert saved.prepare_declared_saved_fixed_lot_session(**kwargs) is result
    assert calls == [kwargs]


@pytest.mark.parametrize('missing', [INPUT, RULE])
def test_incomplete_companion_declaration_fails_before_preparation(monkeypatch, missing):
    release = release_contract()
    draft = replace(release,
        input_contracts=tuple(v for v in release.input_contracts if v != missing),
        rule_set_contracts=tuple(v for v in release.rule_set_contracts if v != missing),
        approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    monkeypatch.setattr(saved, 'numbered_strategy', lambda number: release)
    for module in (legacy, selected):
        monkeypatch.setattr(module, 'prepare_fixed_structural_lot_session',
            lambda **kwargs: pytest.fail('Incomplete declaration admitted'))
    with pytest.raises(ValueError, match='paired declarations'):
        saved.prepare_declared_saved_fixed_lot_session(**arguments())


@pytest.mark.parametrize('number', [True, 0, -1, '109'])
def test_wrong_identity_type_or_range_rejected(number):
    kwargs = arguments()
    kwargs['number'] = number
    with pytest.raises(ValueError, match='registered saved strategy identity'):
        saved.prepare_declared_saved_fixed_lot_session(**kwargs)
