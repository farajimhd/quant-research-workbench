from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from services.reference_gateway.providers import MassiveReferenceClient, MassiveTickerResult
from services.reference_gateway.ticker_events import (
    CanonicalBinding,
    TickerEventEntity,
    build_symbol_intervals,
    entity_shard,
    normalize_inventory,
    normalize_ticker_event_response,
    point_in_time_symbol_sql,
    reconcile_source_binding,
    refresh_ticker_event_inventory,
    select_entities,
    tombstone_rows,
)
from services.reference_gateway.providers import MassiveTickerEventsResult


META_FIGI = "BBG000MM2P62"


def meta_entity() -> TickerEventEntity:
    return TickerEventEntity(
        provider_entity_key=f"massive:composite_figi:{META_FIGI}",
        provider_identifier_kind="composite_figi",
        provider_identifier=META_FIGI,
        current_ticker="META",
        entity_name="Meta Platforms, Inc. Class A Common Stock",
        active=True,
        composite_figi=META_FIGI,
        share_class_figi="BBG001SQCQC5",
        cik="0001326801",
        primary_exchange="XNAS",
        currency_name="USD",
        provider_last_updated_utc="2026-08-01 00:00:00.000",
        source_payload_json="{}",
        source_content_sha256="abc",
    )


class TickerEventTests(unittest.TestCase):
    def test_inventory_deduplicates_by_stable_figi_and_prefers_active_row(self) -> None:
        rows = normalize_inventory(
            [{"ticker": "META", "active": True, "composite_figi": META_FIGI, "name": "Meta"}],
            [{"ticker": "FB", "active": False, "composite_figi": META_FIGI, "name": "Facebook"}],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].current_ticker, "META")
        self.assertEqual(rows[0].provider_identifier_kind, "composite_figi")

    def test_saturated_inventory_preserves_existing_authority(self) -> None:
        provider = Mock()
        provider.fetch_us_stock_tickers.side_effect = [
            MassiveTickerResult(tickers=[], pages=1, saturated=True),
            MassiveTickerResult(tickers=[], pages=1, saturated=False),
        ]
        client = Mock()

        result = refresh_ticker_event_inventory(client, provider, database="q_live", execute=True)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.rows_written, 0)
        self.assertEqual(result.rows_deleted, 0)
        client.execute.assert_not_called()

    def test_meta_provider_timeline_builds_exact_non_overlapping_intervals(self) -> None:
        response = MassiveTickerEventsResult(
            identifier=META_FIGI,
            name="Meta Platforms, Inc. Class A Common Stock",
            status="OK",
            request_id="request-1",
            events=[
                {"date": "2022-06-09", "ticker_change": {"ticker": "META"}, "type": "ticker_change"},
                {"date": "2012-05-18", "ticker_change": {"ticker": "FB"}, "type": "ticker_change"},
            ],
        )

        events, intervals, _ = normalize_ticker_event_response(
            meta_entity(),
            response,
            CanonicalBinding("mapped", "security:meta", "listing:meta", "exact_composite_figi"),
            run_id="test",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(
            [(row["ticker"], row["valid_from_date"], row["valid_to_date_exclusive"], row["is_current"]) for row in intervals],
            [("FB", "2012-05-18", "2022-06-09", 0), ("META", "2022-06-09", None, 1)],
        )
        self.assertTrue(all(row["security_id"] == "security:meta" for row in intervals))

    def test_ambiguous_binding_never_publishes_canonical_intervals(self) -> None:
        intervals = build_symbol_intervals(
            meta_entity(),
            CanonicalBinding("ambiguous", reason="multiple listings"),
            [(datetime(2022, 6, 9, tzinfo=UTC).date(), "META", "event:1")],
            run_id="test",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        )

        self.assertEqual(intervals, [])

    def test_conflicting_same_day_changes_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicting ticker changes"):
            build_symbol_intervals(
                meta_entity(),
                CanonicalBinding("mapped", "security:meta", "listing:meta"),
                [
                    (datetime(2022, 6, 9, tzinfo=UTC).date(), "META", "event:1"),
                    (datetime(2022, 6, 9, tzinfo=UTC).date(), "FB", "event:2"),
                ],
                run_id="test",
                observed_at=datetime(2026, 8, 2, tzinfo=UTC),
            )

    def test_empty_ticker_change_closes_the_previous_symbol_without_creating_blank_interval(self) -> None:
        intervals = build_symbol_intervals(
            meta_entity(),
            CanonicalBinding("mapped", "security:emms", "listing:emms"),
            [
                (datetime(2016, 7, 11, tzinfo=UTC).date(), "EMMS", "event:start"),
                (datetime(2023, 11, 18, tzinfo=UTC).date(), "", "event:end"),
            ],
            run_id="test",
            observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        )

        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["ticker"], "EMMS")
        self.assertEqual(intervals[0]["valid_to_date_exclusive"], "2023-11-18")
        self.assertEqual(intervals[0]["is_current"], 0)

    def test_removed_provider_event_becomes_tombstone(self) -> None:
        existing = [{"ticker_event_id": "event:old", "provider_entity_key": "entity:1", "is_deleted": 0}]

        rows = tombstone_rows(existing, set(), id_column="ticker_event_id", run_id="repair")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["is_deleted"], 1)
        self.assertEqual(rows[0]["source_run_id"], "repair")

    def test_rolling_selection_prioritizes_missing_failed_and_oldest(self) -> None:
        first = meta_entity()
        second = replace(first, provider_entity_key="massive:composite_figi:SECOND", provider_identifier="SECOND", current_ticker="ZZZ")
        coverage = {
            second.provider_entity_key: {
                "source_status": "completed",
                "mapping_status": "mapped",
                "last_success_at_utc": "2026-08-02 00:00:00.000",
            }
        }

        selected = select_entities([second, first], coverage, mode="rolling", max_entities=1, stale_after_days=7, only_identifiers=())

        self.assertEqual([row.provider_entity_key for row in selected], [first.provider_entity_key])

    def test_historical_selection_retries_failure_before_missing(self) -> None:
        failed = meta_entity()
        missing = replace(failed, provider_entity_key="massive:composite_figi:MISSING", provider_identifier="MISSING")
        coverage = {failed.provider_entity_key: {"source_status": "failed", "mapping_status": "mapped"}}

        selected = select_entities([missing, failed], coverage, mode="historical", max_entities=1, stale_after_days=7, only_identifiers=())

        self.assertEqual([row.provider_entity_key for row in selected], [failed.provider_entity_key])

    def test_deterministic_shards_are_disjoint_and_complete(self) -> None:
        entities = [replace(meta_entity(), provider_entity_key=f"entity:{index}") for index in range(100)]
        shards = [
            select_entities(
                entities,
                {},
                mode="historical",
                max_entities=0,
                stale_after_days=7,
                only_identifiers=(),
                shard_index=shard_index,
                shard_count=4,
            )
            for shard_index in range(4)
        ]

        keys = [{entity.provider_entity_key for entity in shard} for shard in shards]
        self.assertEqual(set().union(*keys), {entity.provider_entity_key for entity in entities})
        self.assertEqual(sum(len(key_set) for key_set in keys), 100)
        self.assertTrue(all(entity_shard(key, 4) == index for index, key_set in enumerate(keys) for key in key_set))

    def test_point_in_time_resolver_uses_half_open_intervals(self) -> None:
        sql = point_in_time_symbol_sql(database="q_live", ticker_expression="'FB'", date_expression="toDate('2022-06-08')")

        self.assertIn("valid_from_date <=", sql)
        self.assertIn("valid_to_date_exclusive IS NULL OR", sql)
        self.assertIn("< valid_to_date_exclusive", sql)

    def test_inventory_event_conflict_blocks_canonical_intervals(self) -> None:
        response = MassiveTickerEventsResult(
            identifier=META_FIGI,
            name="Meta",
            events=[{"date": "2023-11-18", "type": "ticker_change", "ticker_change": {"ticker": "S127"}}],
            status="OK",
            request_id="req",
        )

        binding = reconcile_source_binding(meta_entity(), response, CanonicalBinding("mapped", "security:meta", "listing:meta"))
        _, intervals, _ = normalize_ticker_event_response(
            meta_entity(), response, binding, run_id="test", observed_at=datetime(2026, 8, 2, tzinfo=UTC)
        )

        self.assertEqual(binding.status, "source_conflict")
        self.assertEqual(intervals, [])

    def test_provider_fetches_experimental_endpoint_by_stable_identifier(self) -> None:
        client = MassiveReferenceClient(base_url="https://api.massive.com", api_key="secret", page_limit=1000, max_pages=10)
        payload = {
            "status": "OK",
            "request_id": "req",
            "results": {"name": "Meta", "events": [{"date": "2022-06-09", "type": "ticker_change", "ticker_change": {"ticker": "META"}}]},
        }
        with patch("services.reference_gateway.providers.fetch_json", return_value=payload) as fetch:
            result = client.fetch_ticker_events(META_FIGI)

        self.assertEqual(result.events[0]["ticker_change"]["ticker"], "META")
        self.assertIn(f"/vX/reference/tickers/{META_FIGI}/events?", fetch.call_args.args[0])
        self.assertTrue(fetch.call_args.kwargs["allow_not_found"])
        self.assertIn("types=ticker_change", fetch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
