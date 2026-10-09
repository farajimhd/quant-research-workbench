"""Admission routing checks only; no installed source or market fixtures."""
from dataclasses import replace

import pytest

from src.backend import backtest_fixed_structural_lot_execution_v19 as subject
from src.trading_runtime import strategy_registry
from src.trading_runtime.selected_exit_publication_policy import INPUT, RULE
from src.trading_runtime.strategy_ninety_nine_release import release_contract


def arguments():
    return dict(plans=object(), number=98, run_id='controlled', session_date=None,
                market=object(), candidates=object(), entry=object(), seeds=object(),
                through_boundary_ms=100, client_factory=object())


def test_unselected_release_delegates_exact_arguments_to_original_v18(monkeypatch):
    supplied = arguments()
    observed = []
    sentinel = object()
    monkeypatch.setattr(subject.legacy, 'prepare_fixed_structural_lot_session',
                        lambda **kwargs: observed.append(kwargs) or sentinel)
    assert subject.prepare_fixed_structural_lot_session(**supplied) is sentinel
    assert observed == [supplied]


@pytest.mark.parametrize('missing', ('input', 'rule'))
def test_partial_exit_declaration_fails_before_preparation(monkeypatch, missing):
    release = release_contract()
    draft = replace(release,
        input_contracts=tuple(v for v in release.input_contracts if v != INPUT)
            if missing == 'input' else release.input_contracts,
        rule_set_contracts=tuple(v for v in release.rule_set_contracts if v != RULE)
            if missing == 'rule' else release.rule_set_contracts,
        approved_digest='')
    invalid = replace(draft, approved_digest=draft.digest())
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda number: invalid)
    monkeypatch.setattr(subject.legacy, 'prepare_fixed_structural_lot_session',
                        lambda **kwargs: pytest.fail('Partial capability borrowed v18'))
    with pytest.raises(ValueError, match='exact source'):
        subject.prepare_fixed_structural_lot_session(**arguments())
