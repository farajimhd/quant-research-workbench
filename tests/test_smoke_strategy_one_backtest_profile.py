"""The workstation smoke probe reports causal V7 reads separately."""

from scripts.clickhouse.smoke_strategy_one_backtest import (
    _SqlCallProfile, _profile_sql_calls,
)


def test_sql_profile_separates_v7_completed_seconds_from_other_market_reads():
    category = _SqlCallProfile.category
    assert category("SELECT ticker FROM arte.bars_v1 WHERE build_id='b' "
                    "AND resolution_ms=1000 AND bucket_index>=1") == (
                        "v7_completed_second_read")
    assert category("SELECT ticker FROM arte.bars_v1 WHERE resolution_ms=100") == (
        "market_or_control_read")
    assert category("INSERT INTO arte.trading_event_v1 (run_id) VALUES ('r')") == (
        "journal_insert")


def test_sql_profile_counts_streaming_v7_reads_without_claiming_latency(
    monkeypatch, capsys,
):
    from research.mlops.clickhouse import ClickHouseHttpClient

    def stream(_client, _sql, *args, **kwargs):
        return iter(({"ticker": "TEST"},))

    monkeypatch.setattr(ClickHouseHttpClient, "iter_json_each_row", stream)
    profile = _SqlCallProfile()
    client = object.__new__(ClickHouseHttpClient)
    with _profile_sql_calls(profile):
        rows = client.iter_json_each_row(
            "SELECT ticker FROM arte.bars_v1 WHERE build_id='b' AND resolution_ms=1000 "
            "AND bucket_index>=1")
        assert list(rows) == [{"ticker": "TEST"}]
    assert ClickHouseHttpClient.iter_json_each_row is stream
    profile.print_summary()
    assert "v7_completed_second_stream: calls=1; timing=not_measured" in (
        capsys.readouterr().out)
