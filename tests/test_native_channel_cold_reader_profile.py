"""Cold readers must select the same declared principal as the native writer."""
from types import SimpleNamespace

import pytest

from src.backend.backtest_fixed_journal_bootstrap import _v4_cold_reader_preflight
from src.trading_runtime.strategy_one_hundred_eighteen_contract import (
    strategy_one_hundred_eighteen_contract,
)


def client(*, principal='backtest_v4_native_channel_runner', readonly='1'):
    contract = strategy_one_hundred_eighteen_contract()
    return SimpleNamespace(
        automatic_ladder_profile=False, entry_spread_risk_profile=True,
        ladder_geometry_policy=None, fixed_structural_lot_profile=None,
        confirmed_original_risk_policy=contract.confirmed_original_risk_policy,
        native_channel_management_policy=contract.native_channel_management_policy,
        execute=lambda sql: readonly if "getSetting('readonly')" in sql else principal,
    )


def test_declared_native_cold_reader_accepts_exact_readonly_principal():
    _v4_cold_reader_preflight(client())


@pytest.mark.parametrize('principal', [
    'backtest_v4_original_risk_entry_cost_runner', 'backtest_v4_runner', 'operator',
])
def test_native_declaration_rejects_other_principals(principal):
    with pytest.raises(RuntimeError, match='unexpected principal'):
        _v4_cold_reader_preflight(client(principal=principal))


def test_native_cold_reader_rejects_writable_connection():
    with pytest.raises(RuntimeError, match='readonly=1'):
        _v4_cold_reader_preflight(client(readonly='0'))


@pytest.mark.parametrize('field,value', [
    ('confirmed_original_risk_policy', None),
    ('native_channel_management_policy', object()),
    ('fixed_structural_lot_profile', object()),
])
def test_native_cold_reader_rejects_missing_or_conflicting_authority(field, value):
    selected = client()
    setattr(selected, field, value)
    with pytest.raises((ValueError, RuntimeError)):
        _v4_cold_reader_preflight(selected)


def test_native_principal_requires_selected_channel_declaration():
    selected = client()
    selected.native_channel_management_policy = None
    with pytest.raises(RuntimeError, match='unexpected principal'):
        _v4_cold_reader_preflight(selected)
