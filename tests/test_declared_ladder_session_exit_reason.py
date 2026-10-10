"""Exercise the installed registry used by cold journal exit verification."""
import pytest

from src.backend.backtest_ladder_source_authority import declared_numbered_session_exit_reasons
from src.trading_runtime.numbered_fixed_strategy import (
    numbered_fixed_strategy, numbered_session_exit_reason,
)
from src.trading_runtime.strategy_registry import (
    initialize_numbered_fixed_strategies, installed_numbered_fixed_strategy_numbers,
)


def test_every_installed_automatic_contract_has_its_exact_session_reason():
    initialize_numbered_fixed_strategies()
    automatic = tuple(number for number in installed_numbered_fixed_strategy_numbers()
        if getattr(numbered_fixed_strategy(number), 'automatic_entry_policy', None) is not None)
    assert 97 in automatic
    legacy = {49: 'strategy_forty_nine_session_exit',
              51: 'strategy_fifty_one_session_exit',
              65: 'strategy_sixty_five_session_exit'}
    expected = frozenset(legacy.get(number, f'strategy_{number}_session_exit') for number in automatic)
    assert declared_numbered_session_exit_reasons() == expected
    for number in automatic:
        assert numbered_fixed_strategy(number).allows_session_exit is True
        assert numbered_session_exit_reason(number) == legacy.get(number, f'strategy_{number}_session_exit')


def test_legacy_reasons_and_absent_or_disabled_admission_are_preserved():
    initialize_numbered_fixed_strategies()
    assert numbered_session_exit_reason(57) == 'strategy_fifty_seven_session_exit'
    with pytest.raises(ValueError):
        numbered_session_exit_reason(1)
    with pytest.raises(ValueError):
        numbered_session_exit_reason(999999)
    with pytest.raises(ValueError):
        numbered_session_exit_reason(True)
