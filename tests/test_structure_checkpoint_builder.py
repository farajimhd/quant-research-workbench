from __future__ import annotations

from datetime import date
import json

from scripts.build_structure_level_checkpoints import (
    checkpoint_schedule,
    is_retryable_error,
    is_no_history_error,
    load_tickers,
)
from scripts.plan_structure_checkpoint_batches import bootstrap_days, load_tickers as load_plan_tickers


def test_checkpoint_schedule_bounds_historical_bootstrap_gaps() -> None:
    schedule = checkpoint_schedule(
        rebuild_start=date(2026, 2, 20),
        target_start=date(2026, 8, 19),
        target_end=date(2026, 8, 21),
        bootstrap_days=14,
    )

    assert schedule[-3:] == (
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
    )
    assert date(2026, 8, 18) in schedule
    boundaries = (date(2026, 2, 20), *schedule)
    assert max((right - left).days for left, right in zip(boundaries, boundaries[1:])) <= 14
    assert all(value.weekday() < 5 for value in schedule[:-3])
    assert len(schedule) == len(set(schedule))


def test_checkpoint_schedule_needs_no_bootstrap_for_target_only_window() -> None:
    assert checkpoint_schedule(
        rebuild_start=date(2026, 8, 19),
        target_start=date(2026, 8, 19),
        target_end=date(2026, 8, 21),
        bootstrap_days=14,
    ) == (
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
    )


def test_checkpoint_schedule_can_cold_rebuild_directly_into_target_window() -> None:
    assert checkpoint_schedule(
        rebuild_start=date(2026, 2, 20),
        target_start=date(2026, 8, 20),
        target_end=date(2026, 8, 21),
        bootstrap_days=0,
    ) == (
        date(2026, 8, 20),
        date(2026, 8, 21),
    )


def test_retry_classifier_retries_transport_but_not_contract_failures() -> None:
    assert is_retryable_error(
        "QMD History historical checkpoint request failed: error sending request for url"
    )
    assert is_retryable_error("connection reset by peer")
    assert not is_retryable_error("checkpoint algorithm_version mismatch")
    assert not is_retryable_error("rebuild exceeded event limit 50000000")


def test_pre_listing_no_history_is_skippable_not_a_blocked_ticker() -> None:
    error = (
        'HTTP 502 {"error_code":"structure_checkpoint_source_unavailable",'
        '"error":"Generic Structure rebuild found no canonical events for NEW"}'
    )
    assert is_no_history_error(error)


def test_load_tickers_merges_scanner_response_and_inline_values(tmp_path) -> None:
    ticker_file = tmp_path / "scanner.json"
    ticker_file.write_text(
        json.dumps({"rows": [{"symbol": "sugp"}, {"ticker": "NOK"}, {"symbol": "SUGP"}]}),
        encoding="utf-8",
    )

    assert load_tickers(inline=[" clsk "], ticker_files=[str(ticker_file)]) == (
        "CLSK",
        "NOK",
        "SUGP",
    )


def test_load_tickers_accepts_newline_file(tmp_path) -> None:
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("sugp\n\nNOK\nsugp\n", encoding="utf-8")

    assert load_tickers(inline=None, ticker_files=[str(ticker_file)]) == ("NOK", "SUGP")


def test_load_tickers_unions_multiple_session_files(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"rows": [{"symbol": "SUGP"}]}), encoding="utf-8")
    second.write_text(
        json.dumps({"rows": [{"symbol": "NOK"}, {"symbol": "SUGP"}]}),
        encoding="utf-8",
    )

    assert load_tickers(inline=None, ticker_files=[str(first), str(second)]) == (
        "NOK",
        "SUGP",
    )


def test_adaptive_checkpoint_plan_uses_direct_rebuild_for_sparse_ticker() -> None:
    assert bootstrap_days(total=3_499_999, maximum_session=200_000, event_budget=3_500_000) == 0


def test_adaptive_checkpoint_plan_uses_largest_safe_calendar_bucket() -> None:
    assert bootstrap_days(total=10_000_000, maximum_session=100_000, event_budget=3_500_000) == 28
    assert bootstrap_days(total=10_000_000, maximum_session=1_000_000, event_budget=3_500_000) == 3


def test_adaptive_plan_loads_scanner_row_universes(tmp_path) -> None:
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({"rows": [{"symbol": "sugp"}, {"ticker": "NOK"}]}), encoding="utf-8")
    assert load_plan_tickers([path]) == ["NOK", "SUGP"]
