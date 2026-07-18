from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

from pipelines.news.benzinga.news_reaction_extract import (
    HORIZONS,
    LABEL_VERSION,
    STATS_VERSION,
    build_calendar_rows,
    calendar_month_chunks,
    expected_event_tables,
    feature_insert_sql,
    event_source_table,
    execute_reaction_chunk,
    event_cache_create_sql,
    event_coverage_sql,
    monitored_execute,
    is_clickhouse_memory_limit,
    parse_args,
    reaction_insert_sql,
    reaction_news_shard_count,
    stats_insert_sql,
    settings_sql,
    target_table_sql,
    validate_args,
)
from pipelines.news.benzinga.news_reaction_phrase_dictionary import PHRASE_RULES, validate_phrase_rules


class NewsReactionExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.args = parse_args([])

    def test_dictionary_has_unique_canonical_presence_rules(self) -> None:
        validate_phrase_rules()
        self.assertEqual(len(PHRASE_RULES), len({rule.phrase_id for rule in PHRASE_RULES}))
        self.assertGreater(len(PHRASE_RULES), 75)
        self.assertGreater(sum(len(rule.needles) for rule in PHRASE_RULES), 255)

    def test_feature_sql_batches_search_without_occurrence_storage(self) -> None:
        sql = feature_insert_sql(self.args, dt.date(2019, 1, 1), dt.date(2019, 2, 1))
        multi_rules = sum(1 for rule in PHRASE_RULES if len(rule.needles) > 1)
        single_rules = len(PHRASE_RULES) - multi_rules
        self.assertEqual(sql.count("multiSearchAnyCaseInsensitiveUTF8"), multi_rules * 4)
        self.assertEqual(sql.count("positionCaseInsensitiveUTF8"), single_rules * 4)
        self.assertEqual(sql.count("tuple('"), len(PHRASE_RULES))
        self.assertIn("ARRAY JOIN arrayFilter", sql)
        self.assertNotIn("multiSearchAllPositionsCaseInsensitiveUTF8", sql)
        self.assertNotIn("GROUP BY canonical_news_id", sql)
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
        self.assertIn("AND (ticker = 'SPY' OR ticker IN", sql)
        self.assertNotIn("upperUTF8(ticker) = 'SPY'", sql)
        self.assertIn("update_last_tokens", sql)
        self.assertIn("update_high_low_tokens", sql)
        self.assertIn("fully_price_eligible_tokens", sql)
        self.assertIn("modifier_int = 12", sql)
        self.assertIn("point_arrays AS", sql)
        self.assertIn("arraySort(event -> tupleElement(event, 1), groupArrayIf", sql)
        self.assertNotIn("ASOF", sql)
        self.assertNotIn("intraday_base_bars", sql)
        self.assertNotIn("label_resolution_us", sql)
        self.assertNotIn("bucket_index", sql.split("news_base AS", 1)[0])
        self.assertNotIn("quote_bid", sql)
        self.assertNotIn("quote_ask", sql)
        self.assertNotIn("nbbo_mid", sql)
        self.assertIn("tupleElement(event, 1) < pub_us", sql)
        self.assertIn("tupleElement(event, 1) > pub_us", sql)
        self.assertIn("tupleElement(event, 1) <= target_us, market_events.all_market_events", sql)
        self.assertIn("arrayFilter(event -> tupleElement(event, 1) <= day_window.max_target_us", sql)
        self.assertIn("asset_event_sets AS", sql)
        self.assertIn("ON day_window.ticker = p.ticker", sql)
        self.assertIn("ifNull(o.overlapping_news_count, toUInt32(0)) AS overlap_count", sql)
        self.assertIn("overlap_count AS overlapping_news_count", sql)
        self.assertNotIn("ifNull(overlapping_news_count, 0) AS overlapping_news_count", sql)
        self.assertIn("arrayMax(event -> if(", sql)
        self.assertIn("arrayMin(event -> if(", sql)
        self.assertIn("arrayCount(event ->", sql)
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
        self.assertEqual(self.args.reaction_ticker_shards, 32)
        self.assertEqual(self.args.reaction_links_per_shard, 100)
        self.assertEqual(self.args.reaction_max_news_shards, 64)
        self.assertEqual(self.args.reaction_chunk_days, 1)
        self.assertEqual(self.args.max_threads // self.args.reaction_workers, 2)
        self.assertEqual(self.args.max_memory_usage, "24G")
        bounded_settings = settings_sql(self.args, experimental_join=True)
        self.assertIn("max_block_size = 1024", bounded_settings)
        self.assertIn("max_joined_block_size_rows = 1024", bounded_settings)

    def test_event_cache_is_sharded_and_reused_by_reaction_query(self) -> None:
        cache_name = "_news_reaction_event_cache_test"
        cache_sql = event_cache_create_sql(
            self.args,
            dt.date(2019, 1, 2),
            dt.date(2019, 1, 3),
            cache_name,
            ticker_shard_index=3,
            ticker_shard_count=8,
        )
        self.assertIn(f"CREATE TABLE `q_live`.`{cache_name}`", cache_sql)
        self.assertIn("ENGINE = MergeTree", cache_sql)
        self.assertIn("cityHash64(upperUTF8(ticker)) % toUInt64(8) = toUInt64(3)", cache_sql)
        self.assertNotIn("cityHash64(canonical_news_id, upperUTF8(ticker))", cache_sql)
        self.assertNotIn("window_event_dates", cache_sql)
        cached_reaction_sql = reaction_insert_sql(
            self.args,
            dt.date(2019, 1, 2),
            dt.date(2019, 1, 3),
            ticker_shard_index=3,
            ticker_shard_count=8,
            event_cache_table_name=cache_name,
        )
        self.assertIn(f"FROM `q_live`.`{cache_name}`", cached_reaction_sql)
        self.assertIn("cityHash64(t.canonical_news_id, upperUTF8(t.ticker)) % toUInt64(8) = toUInt64(3)", cached_reaction_sql)
        self.assertIn("active_cache_tickers AS", cached_reaction_sql)
        self.assertEqual(
            cached_reaction_sql.count("ticker IN (SELECT ticker FROM active_cache_tickers)"),
            2,
        )
        self.assertIn("AND event_date IN (SELECT window_event_date FROM window_event_dates)", cached_reaction_sql)
        self.assertIn("ticker = 'SPY'", cached_reaction_sql)
        self.assertIn("WHERE event_date < toDate('2019-01-02')", cached_reaction_sql)
        self.assertLess(
            cached_reaction_sql.index("window_event_dates AS"),
            cached_reaction_sql.index("active_cache_tickers AS"),
        )

        append_sql = event_cache_create_sql(
            self.args,
            dt.date(2019, 1, 1),
            dt.date(2019, 2, 1),
            cache_name,
            ticker_shard_index=1,
            ticker_shard_count=8,
            create_table=False,
            include_benchmark=False,
        )
        self.assertIn(f"INSERT INTO `q_live`.`{cache_name}`", append_sql)
        self.assertNotIn("CREATE TABLE", append_sql)
        self.assertNotIn("ticker = 'SPY' OR ticker IN", append_sql)

    def test_sparse_news_days_use_dynamic_bounded_shards(self) -> None:
        self.assertEqual(reaction_news_shard_count(self.args, 1), 1)
        self.assertEqual(reaction_news_shard_count(self.args, 100), 1)
        self.assertEqual(reaction_news_shard_count(self.args, 101), 2)
        self.assertEqual(reaction_news_shard_count(self.args, 100_000), 64)
        self.assertTrue(is_clickhouse_memory_limit(RuntimeError("MEMORY_LIMIT_EXCEEDED")))
        self.assertTrue(is_clickhouse_memory_limit(RuntimeError("Query memory limit exceeded")))
        self.assertFalse(is_clickhouse_memory_limit(RuntimeError("network reset")))

    def test_reaction_chunk_retries_memory_failure_with_finer_news_shards(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.query_ids: list[str] = []
                self.failed = False

            def execute(self, sql: str, *, query_id: str | None = None) -> str:
                if query_id:
                    self.query_ids.append(query_id)
                if query_id and query_id.endswith("attempt_00_shard_000") and not self.failed:
                    self.failed = True
                    raise RuntimeError("MEMORY_LIMIT_EXCEEDED")
                if "SELECT count()" in sql:
                    return "20"
                return ""

        fake = FakeClient()
        with patch(
            "pipelines.news.benzinga.news_reaction_extract.ClickHouseHttpClient",
            return_value=fake,
        ):
            result = execute_reaction_chunk(
                self.args,
                dt.date(2019, 1, 2),
                dt.date(2019, 1, 3),
                query_threads=2,
                query_memory=6 * 1024**3,
                query_id="retry-test",
                cache_table_name="_cache",
                news_shard_count=2,
            )
        self.assertEqual(result.inserted_rows, 20)
        self.assertIn("retry-test_attempt_00_reset", fake.query_ids)
        self.assertIn("retry-test_attempt_01_reset", fake.query_ids)
        self.assertIn("retry-test_attempt_01_shard_003", fake.query_ids)

    def test_reaction_months_use_calendar_boundaries_when_resuming_mid_month(self) -> None:
        chunks = list(calendar_month_chunks(dt.date(2019, 3, 28), dt.date(2019, 5, 2)))
        self.assertEqual(
            chunks,
            [
                (dt.date(2019, 3, 1), dt.date(2019, 4, 1)),
                (dt.date(2019, 4, 1), dt.date(2019, 5, 1)),
                (dt.date(2019, 5, 1), dt.date(2019, 6, 1)),
            ],
        )

    def test_reaction_ticker_shards_must_be_complete(self) -> None:
        with self.assertRaises(ValueError):
            reaction_insert_sql(
                self.args,
                dt.date(2019, 1, 2),
                dt.date(2019, 1, 3),
                ticker_shard_index=0,
            )
        with self.assertRaises(ValueError):
            reaction_insert_sql(
                self.args,
                dt.date(2019, 1, 2),
                dt.date(2019, 1, 3),
                ticker_shard_index=8,
                ticker_shard_count=8,
            )

    def test_reaction_only_range_does_not_require_stats_range(self) -> None:
        args = parse_args([
            "--stages", "reactions",
            "--start-date", "2019-01-02",
            "--end-date", "2019-01-03",
        ])
        validate_args(args, ("reactions",))

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
