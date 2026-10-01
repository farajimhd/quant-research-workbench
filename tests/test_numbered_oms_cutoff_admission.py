"""Exercise the real cutoff admission method that failed the native Strategy 32 run."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.strategy_registry import installed_numbered_fixed_strategy_numbers


def manager(number, *, strategy_id='early-squeeze-strategy', causal=True):
    result = object.__new__(OrderManagementEngine)
    result.strategy_id = strategy_id
    result.strategy_revision = number
    result.causal_execution_clock = causal
    result._groups = {}
    result.reconcile = AsyncMock()
    return result


@pytest.mark.parametrize('number', [n for n in installed_numbered_fixed_strategy_numbers() if n != 1])
def test_every_installed_extended_version_admits_cutoff_even_without_open_groups(number):
    instance = manager(number)
    asyncio.run(instance.cancel_numbered_session_acquisitions(
        at=datetime(2026, 8, 18, 23, 50, tzinfo=timezone.utc)))
    instance.reconcile.assert_not_awaited()


@pytest.mark.parametrize('number', [1, True, 31., '32', 0, -1, 999])
def test_nonextended_untyped_or_uninstalled_identity_is_rejected(number):
    instance = manager(number)
    with pytest.raises(ValueError, match='numbered fixed OMS'):
        asyncio.run(instance.cancel_numbered_session_acquisitions(
            at=datetime(2026, 8, 18, 23, 50, tzinfo=timezone.utc)))
    instance.reconcile.assert_not_awaited()


@pytest.mark.parametrize('changes', [{'strategy_id': 'other'}, {'causal': False}])
def test_registered_number_does_not_bypass_strategy_or_causal_clock(changes):
    instance = manager(32, **changes)
    with pytest.raises(ValueError, match='numbered fixed OMS'):
        asyncio.run(instance.cancel_numbered_session_acquisitions(
            at=datetime(2026, 8, 18, 23, 50, tzinfo=timezone.utc)))
    instance.reconcile.assert_not_awaited()
