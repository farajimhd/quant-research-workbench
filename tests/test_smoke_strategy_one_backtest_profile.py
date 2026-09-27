"""The workstation smoke probe reports causal V7 reads separately."""

from scripts.clickhouse.smoke_strategy_one_backtest import _SqlCallProfile


def test_sql_profile_separates_v7_completed_seconds_from_other_market_reads():
    category = _SqlCallProfile.category
    assert category("SELECT ticker FROM arte.bars_v1 WHERE build_id='b' "
                    "AND resolution_ms=1000 AND bucket_index>=1") == (
                        "v7_completed_second_read")
    assert category("SELECT ticker FROM arte.bars_v1 WHERE resolution_ms=100") == (
        "market_or_control_read")
    assert category("INSERT INTO arte.trading_event_v1 (run_id) VALUES ('r')") == (
        "journal_insert")
