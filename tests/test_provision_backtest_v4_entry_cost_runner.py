from unittest.mock import patch

import pytest

from scripts.clickhouse import provision_backtest_v4_entry_cost_runner as entry_cost
from scripts.clickhouse import provision_backtest_v4_runner as historical
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_entry_spread_risk_v4 import ENTRY_SPREAD_RISK
TABLES = (ENTRY_SPREAD_RISK,)


def test_declared_layout_is_one_separate_normalized_ssd_table():
    from scripts.clickhouse.plan_trading_journal_layout import profile_contracts
    assert profile_contracts('entry-spread-risk-evidence') == TABLES
    ddl=ENTRY_SPREAD_RISK.ddl()
    assert 'live_market_ssd' in ddl
    assert 'JSON' not in ddl and 'Array(' not in ddl
    assert ENTRY_SPREAD_RISK not in writer.v4_storage_contracts()


def test_entry_cost_plan_changes_only_principal_and_one_normalized_families():
    before, after = historical.desired_plan(), entry_cost.desired_plan()
    tables = frozenset(table.name for table in TABLES)
    assert after.principal == 'backtest_v4_entry_cost_runner'
    assert after.insert_arte - before.insert_arte == tables
    assert after.select_arte - before.select_arte == tables
    assert after.insert_arte - tables == before.insert_arte
    assert historical.desired_plan() == before
    assert after.select_system == before.select_system
    assert after.select_reference == before.select_reference


def test_default_dry_run_does_not_open_admin_or_create_credentials(capsys):
    with patch.object(entry_cost, '_admin_client', side_effect=AssertionError), \
            patch.object(entry_cost, 'credential', side_effect=AssertionError):
        assert entry_cost.main([]) == 0
    assert 'Plan only' in capsys.readouterr().out


def test_profile_cannot_reuse_historical_runner_credential():
    with patch.object(writer, '_dedicated_clickhouse_credentials',
            return_value=('http://desktop-saai85t:18123', 'backtest_v4_runner', 'private')):
        with pytest.raises(ValueError, match='dedicated runner'):
            writer._v4_runner_credentials(entry_spread_risk=True)


def test_entry_cost_profile_selects_separate_credential_keys():
    with patch.object(writer, '_dedicated_clickhouse_credentials',
            return_value=('http://desktop-saai85t:18123', 'backtest_v4_entry_cost_runner', 'private')) as read:
        writer._v4_runner_credentials(entry_spread_risk=True)
    read.assert_called_once_with('BACKTEST_V4_ENTRY_COST_RUNNER_CLICKHOUSE_',
        'BACKTEST_V4_ENTRY_COST_RUNNER_CREDENTIAL_FILE')
