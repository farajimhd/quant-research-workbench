from __future__ import annotations

import datetime as dt
import unittest

from pipelines.news.benzinga.news_reaction_extract import (
    HORIZONS,
    LABEL_VERSION,
    MULTISEARCH_NEEDLE_LIMIT,
    STATS_VERSION,
    build_calendar_rows,
    expected_event_tables,
    feature_insert_sql,
    event_source_table,
    event_coverage_sql,
    monitored_execute,
    parse_args,
    reaction_insert_sql,
    stats_insert_sql,
    target_table_sql,
)
from pipelines.news.benzinga.news_reaction_phrase_dictionary import PHRASE_RULES, validate_phrase_rules


class NewsReactionExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.args = parse_args([])

    def test_dictionary_has_unique_canonical_presence_rules(self) -> None:
        validate_phrase_rules()
        self.assertEqual(len(PHRASE_RULES), len({rule.phrase_id for rule in PHRASE_RULES}))
        self.assertGreater(len(PHRASE_RULES), 75)
        self.assertGreater(sum(len(rule.needles) for rule in PHRASE_RULES), MULTISEARCH_NEEDLE_LIMIT)

    def test_feature_sql_batches_search_without_occurrence_storage(self) -> None:
        sql = feature_insert_sql(self.args, dt.date(2019, 1, 1), dt.date(2019, 2, 1))
        batch_count = (sum(len(rule.needles) for rule in PHRASE_RULES) + MULTISEARCH_NEEDLE_LIMIT - 1) // MULTISEARCH_NEEDLE_LIMIT
        self.assertEqual(sql.count("multiSearchAllPositionsCaseInsensitiveUTF8"), batch_count * 4)
        self.assertIn("arrayDistinct", sql)
        self.assertNotIn("occurrence", sql.lower())
        feature_schema = target_table_sql(self.args)[2].lower()
        self.assertNotIn("occurrence", feature_schema)

    def test_reaction_sql_is_strictly_causal_and_retains_window_extrema(self) -> None:
        sql = reaction_insert_sql(self.args, dt.date(2019, 1, 2), dt.date(2019, 1, 3))
        self.assertIn("market_sip_compact", sql)
        self.assertIn("events_2019", sql)
        self.assertNotIn("events_2018", sql)
        self.assertIn("bitAnd(event_meta, 1) = 1", sql)
        self.assertIn("sip_timestamp_us > 0", sql)
        self.assertIn("size_primary > 0", sql)
        self.assertIn("update_last_tokens", sql)
        self.assertIn("update_high_low_tokens", sql)
        self.assertIn("fully_price_eligible_tokens", sql)
        self.assertIn("modifier_int = 12", sql)
        self.assertIn("ASOF LEFT JOIN last_points", sql)
        self.assertNotIn("intraday_base_bars", sql)
        self.assertNotIn("label_resolution_us", sql)
        self.assertNotIn("bucket_index", sql.split("news_base AS", 1)[0])
        self.assertNotIn("quote_bid", sql)
        self.assertNotIn("quote_ask", sql)
        self.assertNotIn("nbbo_mid", sql)
        self.assertIn("w.pub_us >= p.last_trade_timestamp_us + toUInt64(1)", sql)
        self.assertIn("p.first_trade_timestamp_us > a.pub_us", sql)
        self.assertIn("p.last_trade_timestamp_us <= a.target_us", sql)
        self.assertIn("maxIf(toNullable(p.trade_high)", sql)
        self.assertIn("minIf(toNullable(p.trade_low)", sql)
        self.assertIn("'eligible_trade_event' AS price_basis", sql)
        self.assertIn("c.is_session AS is_session", sql)
        self.assertNotIn("trade_fallback", sql)
        self.assertIn("publication_session != 'closed'", sql)
        self.assertIn("<= extended_close_us", sql)
        self.assertIn("overlapping_news", sql)
        reaction_schema = target_table_sql(self.args)[3]
        self.assertNotIn("price_resolution_us", reaction_schema)

    def test_event_label_semantics_are_versioned(self) -> None:
        self.assertEqual(LABEL_VERSION, "news_reaction_event_labels_v3")
        self.assertEqual(STATS_VERSION, "news_phrase_event_reaction_stats_v3")
        self.assertEqual(self.args.reactions_table, "news_reaction_labels_v2")

    def test_event_source_routes_only_required_years(self) -> None:
        source = event_source_table(self.args, dt.date(2025, 12, 31), dt.date(2026, 1, 2))
        self.assertIn("events_2025", source)
        self.assertIn("events_2026", source)
        self.assertNotIn("events_2024", source)
        self.assertEqual(self.args.reaction_workers, 4)
        self.assertEqual(self.args.reaction_chunk_days, 1)
        self.assertEqual(self.args.max_threads // self.args.reaction_workers, 6)
        self.assertEqual(self.args.max_memory_usage, "24G")

    def test_event_authority_is_clamped_to_publication_years(self) -> None:
        expected = expected_event_tables(self.args)
        self.assertEqual(expected[0], "events_2019")
        self.assertEqual(expected[-1], "events_2026")
        self.assertEqual(len(expected), 8)
        first_source = event_source_table(self.args, dt.date(2018, 12, 24), dt.date(2019, 1, 10))
        last_source = event_source_table(self.args, dt.date(2026, 12, 24), dt.date(2027, 1, 9))
        self.assertIn("events_2019", first_source)
        self.assertNotIn("events_2018", first_source)
        self.assertIn("events_2026", last_source)
        self.assertNotIn("events_2027", last_source)

    def test_event_coverage_uses_active_part_metadata(self) -> None:
        sql = event_coverage_sql(self.args, expected_event_tables(self.args))
        self.assertIn("FROM system.parts", sql)
        self.assertIn("sum(rows) AS event_rows", sql)
        self.assertIn("arraySort(groupUniqArray(table)) AS populated_tables", sql)
        self.assertIn("events_2019", sql)
        self.assertIn("events_2026", sql)
        self.assertNotIn("events_2018", sql)
        self.assertNotIn("events_2027", sql)
        self.assertNotIn("market_sip_compact.events", sql)

    def test_monitored_query_interrupt_requests_clickhouse_cancellation(self) -> None:
        class InterruptingClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None]] = []

            def execute(self, sql: str, *, query_id: str | None = None) -> str:
                self.calls.append((sql, query_id))
                if len(self.calls) == 1:
                    raise KeyboardInterrupt
                return ""

        class Reporter:
            def __init__(self) -> None:
                self.query_id = ""
                self.was_interrupted = False

            def query_start(self, label: str, query_id: str) -> None:
                self.query_id = query_id

            def interrupted(self) -> None:
                self.was_interrupted = True

            def message(self, text: str) -> None:
                pass

        client = InterruptingClient()
        reporter = Reporter()
        with self.assertRaises(KeyboardInterrupt):
            monitored_execute(client, "SELECT sleep(10)", reporter, "interrupt test")  # type: ignore[arg-type]
        self.assertTrue(reporter.was_interrupted)
        self.assertTrue(reporter.query_id.startswith("news-reaction-"))
        self.assertIn("KILL QUERY WHERE query_id", client.calls[1][0])

    def test_horizon_contract_and_held_out_year_are_explicit(self) -> None:
        self.assertEqual(
            [code for code, _, _ in HORIZONS],
            ["1m", "5m", "10m", "30m", "1h", "2h", "3h", "premarket_close", "regular_close", "extended_close"],
        )
        self.assertEqual(self.args.stats_end_date, "2026-01-01")
        self.assertEqual(self.args.end_date, "2027-01-01")
        stats_sql = stats_insert_sql(self.args)
        self.assertIn("feature_role != 'observed_reaction'", stats_sql)
        self.assertIn("HAVING countIf(r.quality_status = 'clean') > 0", stats_sql)

    def test_xnys_calendar_uses_early_regular_close(self) -> None:
        rows = build_calendar_rows(dt.date(2025, 11, 28), dt.date(2025, 11, 29))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current_regular_close_utc"], "2025-11-28 18:00:00.000000")
        self.assertEqual(rows[0]["current_extended_close_utc"], "2025-11-29 01:00:00.000000")


if __name__ == "__main__":
    unittest.main()
