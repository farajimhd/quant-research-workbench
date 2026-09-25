from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_market_day_source_plan import (
    TABLES, project_source_plan, recover_source_plan,
)


BUILD = "a" * 64
DAY = "2026-08-18"


def plan():
    return dict(
        sessions=[DAY], requested=[DAY], predecessors={DAY: "2026-08-17"},
        stats=[dict(source_date=DAY, stats_version=1, source_filter_key="canonical",
                    total_event_rows_after_filters=1,
                    updated_at="2026-08-18T21:00:00+00:00")],
        population=[dict(session_date=DAY,
            authority="q_live.feature_tradable_universe_snapshot_v2",
            tradable_tickers=1, snapshot_rows=1, snapshot_hash="members",
            selected_ticker_days=1, excluded_canonical_tickers=0,
            tradable_without_canonical_events=0,
            certificate=dict(snapshot_id="snapshot", source_universe_date=DAY,
                captured_at_utc="2026-08-18T07:00:00+00:00",
                available_at_utc="2026-08-18T07:01:00+00:00",
                cutoff_utc="2026-08-18T08:00:00+00:00",
                row_count=1, tradable_count=1, source_hash=123,
                revision="preopen-tradable-snapshot-v3", status="certified"))],
        units=[dict(source_date=DAY, ticker="TEST", event_count=1,
                    next_ordinal=2, last_ordinal=1,
                    first_sip_timestamp_us=100, last_sip_timestamp_us=100,
                    build_step=739847, updated_at="2026-08-18T21:00:00+00:00")],
        rules=[dict(token_id=1, modifier_int=0, update_high_low=1,
                    update_last=1, update_volume=1)],
        splits=[dict(provider_ticker="CISS", execution_date="2026-08-19",
                     split_from=40, split_to=1,
                     inserted_at="2026-08-21 00:22:04.290")],
        excluded_calendar_dates=["2026-08-19"],
    )


def test_closed_named_source_plan_roundtrips_exactly() -> None:
    source = plan()
    rows = project_source_plan(source, BUILD)
    restored = recover_source_plan(rows, BUILD,
        expected_hash=rows["market_day_source_plan_v1"][0]["source_plan_hash"])
    assert restored == source
    assert all("PARTITION BY toYYYYMM(plan_month)" in table.ddl() for table in TABLES)
    assert all(not any(column in table.ddl().lower() for column in (
        "payload_json", " blob ", " map(")) for table in TABLES)


def test_carried_population_and_nullable_counts_roundtrip() -> None:
    source = plan()
    row = source["population"][0]
    row["certificate"]["status"] = "carried_forward"
    row["certificate"]["revision"] = "preopen-tradable-carry-forward-v1"
    row["certificate"]["source_session_date"] = "2026-08-17"
    row["excluded_canonical_tickers"] = None
    row["tradable_without_canonical_events"] = None
    rows = project_source_plan(source, BUILD)
    assert recover_source_plan(rows, BUILD,
        expected_hash=rows["market_day_source_plan_v1"][0]["source_plan_hash"]) == source


def test_unknown_fields_duplicate_ordinals_and_hash_tampering_fail_closed() -> None:
    source = plan()
    source["units"][0]["unmodeled"] = 1
    with pytest.raises(ValueError, match="unmodeled named fields"):
        project_source_plan(source, BUILD)
    rows = project_source_plan(plan(), BUILD)
    mutable = {name: [dict(row) for row in family] for name, family in rows.items()}
    mutable["market_day_source_rule_v1"].append(
        deepcopy(mutable["market_day_source_rule_v1"][0]))
    with pytest.raises(ValueError, match="inventory"):
        recover_source_plan(mutable, BUILD,
            expected_hash=rows["market_day_source_plan_v1"][0]["source_plan_hash"])
    mutable = {name: [dict(row) for row in family] for name, family in rows.items()}
    mutable["market_day_source_unit_v1"][0]["event_count"] = 2
    with pytest.raises(ValueError, match="inventory"):
        recover_source_plan(mutable, BUILD,
            expected_hash=rows["market_day_source_plan_v1"][0]["source_plan_hash"])


def test_split_scalar_type_is_not_silently_coerced() -> None:
    source = plan()
    source["splits"][0]["split_from"] = 1.0
    with pytest.raises(ValueError, match="bounded integer"):
        project_source_plan(source, BUILD)


def test_real_split_shape_preserves_integer_ratio_and_timestamp_text() -> None:
    source = plan()
    rows = project_source_plan(source, BUILD)
    saved = rows["market_day_source_split_v1"][0]
    assert (saved["provider_ticker"], saved["execution_date"],
            saved["split_from"], saved["split_to"], saved["inserted_at"]) == (
                "CISS", "2026-08-19", 40, 1, "2026-08-21 00:22:04.290")
    assert recover_source_plan(rows, BUILD,
        expected_hash=rows["market_day_source_plan_v1"][0]["source_plan_hash"]) == source
