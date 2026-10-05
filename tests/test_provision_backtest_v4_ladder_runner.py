from unittest.mock import patch

import pytest

from scripts.clickhouse import provision_backtest_v4_ladder_runner as ladder
from scripts.clickhouse import provision_backtest_v4_runner as historical
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_squeeze_ladder_schema import TABLES


def test_ladder_plan_changes_only_principal_and_two_normalized_families():
    before, after = historical.desired_plan(), ladder.desired_plan()
    tables = frozenset(table.name for table in TABLES)
    assert after.principal == 'backtest_v4_ladder_runner'
    assert after.insert_arte - before.insert_arte == tables
    assert after.select_arte - before.select_arte == tables
    assert after.insert_arte - tables == before.insert_arte
    assert historical.desired_plan() == before
    assert after.select_system == before.select_system
    assert after.select_reference == before.select_reference


def test_default_dry_run_does_not_open_admin_or_create_credentials(capsys):
    with patch.object(ladder, '_admin_client', side_effect=AssertionError), \
            patch.object(ladder, 'credential', side_effect=AssertionError):
        assert ladder.main([]) == 0
    assert 'Plan only' in capsys.readouterr().out


def test_profile_cannot_reuse_historical_runner_credential():
    with patch.object(writer, '_dedicated_clickhouse_credentials',
            return_value=('http://desktop-saai85t:18123', 'backtest_v4_runner', 'private')):
        with pytest.raises(ValueError, match='dedicated runner'):
            writer._v4_runner_credentials(automatic_ladder=True)


def test_ladder_profile_selects_separate_credential_keys():
    with patch.object(writer, '_dedicated_clickhouse_credentials',
            return_value=('http://desktop-saai85t:18123', 'backtest_v4_ladder_runner', 'private')) as read:
        writer._v4_runner_credentials(automatic_ladder=True)
    read.assert_called_once_with('BACKTEST_V4_LADDER_RUNNER_CLICKHOUSE_',
        'BACKTEST_V4_LADDER_RUNNER_CREDENTIAL_FILE')
