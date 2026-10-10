"""Denial-only controls; these do not qualify a positively issued read."""
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_lot_initial_recovery_reuse as initial
from src.backend.backtest_fixed_lot_first_inventory_source_reuse import (
    load_first_inventory, selected_first_inventory_source_policy,
)


@pytest.mark.parametrize('operation', (
    None, SimpleNamespace(require=lambda: None),
    initial._Initial.__new__(initial._Initial),
))
def test_unissued_operation_cannot_execute_loader(operation):
    calls = []
    with pytest.raises(ValueError, match='genuine issued operation'):
        load_first_inventory(operation, lambda: calls.append('loaded'), inventory_key=())
    assert not calls


def test_absent_installed_publication_does_not_select_source_reuse():
    source = SimpleNamespace(installed_payload=None)
    assert selected_first_inventory_source_policy(source, None) is None
