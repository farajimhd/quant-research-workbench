from __future__ import annotations

import unittest
import json
import multiprocessing
import os
import queue
import tempfile
import concurrent.futures
import time
from pathlib import Path
from unittest import mock

from research.mlops.clickhouse import ClickHouseHttpClient
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .news_extractor import (
    analyze_news_scope,
    extract_news_units,
    extract_observed_reaction,
)
from .news_identity import IssuerIdentity, NewsIssuerResolver, _identity_rows
from .pipeline import classify_news_document, classify_sec_document
from .sec_extractor import extract_sec_units
from .persistence import (
    MAX_EXACT_DOCUMENT_KEY_SQL_BYTES,
    JsonInsertBuffer,
    PermanentPayloadError,
    _bounded_document_key_sql,
    attach_sec_ticker,
    assert_certification,
    bounded_period_ranges,
    cleanup_inflight_statuses,
    compact_semantic_payload,
    execute_bounded_plan,
    interleaved_plan,
    iter_bounded_source_batches,
    iter_news_period,
    iter_sec_documents_for_filings,
    is_transient_clickhouse_error,
    parse_args,
    relationship_rows,
)
from .schema import SCOPED_LABELING_VERSION


def process_worker_probe() -> tuple[int, str, bool]:
    from . import persistence

    persistence.publish_worker_progress(
        corpus="news",
        start="2026-07-01",
        stage="source",
        source_rows=1,
        label_rows=0,
        relation_rows=0,
    )
    return (
        os.getpid(),
        persistence._WORKER_DATABASE,
        persistence._stop_requested(),
    )


class ScopedLabelingTests(unittest.TestCase):
    def test_financial_acronym_exchange_pair_is_not_an_issuer(self) -> None:
        text = (
            "Blackstone (NYSE:BX) reported second-quarter economic net income "
            "(NYSE:ENI) of $1.15 per share."
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("BX", "issuer:bx", ("Blackstone",)),
                IssuerIdentity("ENI", "issuer:eni", ("Eni S.p.A.",)),
            ),
            article_tickers=("BX", "ENI"),
        )
        analysis = analyze_news_scope(
            source_id="acronym",
            title="Blackstone Reports Results",
            text=text,
            tickers=("BX", "ENI"),
            timestamp="2014-07-24T12:00:00Z",
            issuer_resolver=resolver,
        )
        self.assertEqual([unit.tickers[0] for unit in analysis.units], ["BX"])

    def test_passive_acquisition_assigns_target_and_acquirer(self) -> None:
        text = (
            "NPS Pharmaceuticals (NASDAQ:NPSP) announced it was being acquired "
            "by Shire plc (NASDAQ:SHPG) for $5.2 billion."
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("NPSP", "issuer:npsp", ("NPS Pharmaceuticals",)),
                IssuerIdentity("SHPG", "issuer:shpg", ("Shire plc",)),
            ),
            article_tickers=("NPSP", "SHPG"),
        )
        units = extract_news_units(
            source_id="passive-ma",
            title="Shire To Acquire NPS Pharmaceuticals",
            text=text,
            tickers=("NPSP", "SHPG"),
            timestamp="2015-01-12T12:00:00Z",
            issuer_resolver=resolver,
        )
        roles = {unit.tickers[0]: unit.issuer_role for unit in units}
        self.assertEqual(roles, {"NPSP": "target", "SHPG": "acquirer"})

    @staticmethod
    def issuer_resolver() -> NewsIssuerResolver:
        return NewsIssuerResolver(
            (
                IssuerIdentity(
                    ticker="EXMP",
                    issuer_id="issuer-example",
                    aliases=(
                        "Example Therapeutics, Inc.",
                        "Example Therapeutics",
                        "Example",
                    ),
                ),
                IssuerIdentity(
                    ticker="EXM.A",
                    issuer_id="issuer-example",
                    aliases=("Example Therapeutics Class A",),
                ),
                IssuerIdentity(
                    ticker="OTHR",
                    issuer_id="issuer-other",
                    aliases=("Other Corp",),
                ),
                IssuerIdentity(
                    ticker="AAPL",
                    issuer_id="issuer-apple",
                    aliases=("Apple Inc.", "Apple"),
                ),
                IssuerIdentity(
                    ticker="GS",
                    issuer_id="issuer-goldman",
                    aliases=("Goldman Sachs Group, Inc.", "Goldman Sachs"),
                ),
            )
        )

    def test_persistence_windows_are_bounded_and_exact(self) -> None:
        self.assertEqual(
            bounded_period_ranges("2026-07-01", "2026-07-12", 7),
            [
                ("2026-07-01", "2026-07-08"),
                ("2026-07-08", "2026-07-12"),
            ],
        )

    def test_clickhouse_json_each_row_is_streamed(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter((b'{"value":1}\n', b'{"value":2}\n'))

            def read(self):
                raise AssertionError("streaming path must not materialize")

        client = ClickHouseHttpClient("http://localhost:8123", "", "")
        with mock.patch(
            "research.mlops.clickhouse.request.urlopen",
            return_value=Response(),
        ):
            rows = list(
                client.iter_json_each_row(
                    "SELECT 1 FORMAT JSONEachRow"
                )
            )
        self.assertEqual(rows, [{"value": 1}, {"value": 2}])

    def test_clickhouse_stream_requires_json_each_row_contract(self) -> None:
        client = ClickHouseHttpClient("http://localhost:8123", "", "")
        with self.assertRaisesRegex(ValueError, "FORMAT JSONEachRow"):
            list(client.iter_json_each_row("SELECT 1 FORMAT TSV"))

    def test_clickhouse_stream_surfaces_server_exception(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter((b'{"value":1}\n', b"__exception__\n"))

            def read(self):
                return b"Code: 241. DB::Exception: memory limit"

        client = ClickHouseHttpClient("http://localhost:8123", "", "")
        with mock.patch(
            "research.mlops.clickhouse.request.urlopen",
            return_value=Response(),
        ):
            with self.assertRaisesRegex(RuntimeError, "memory limit"):
                list(
                    client.iter_json_each_row(
                        "SELECT 1 FORMAT JSONEachRow"
                    )
                )

    def test_persistence_interleaves_corpora_by_period(self) -> None:
        periods = [
            ("2026-07-01", "2026-07-08"),
            ("2026-07-08", "2026-07-15"),
        ]
        self.assertEqual(
            interleaved_plan(("news", "sec"), periods),
            [
                ("news", "2026-07-01", "2026-07-08"),
                ("sec", "2026-07-01", "2026-07-08"),
                ("news", "2026-07-08", "2026-07-15"),
                ("sec", "2026-07-08", "2026-07-15"),
            ],
        )

    def test_persistence_accepts_sixty_four_workers(self) -> None:
        self.assertEqual(parse_args(["--workers", "64"]).workers, 64)

    def test_news_query_bounds_final_inputs_before_join(self) -> None:
        class CaptureClient:
            sql = ""

            def iter_json_each_row(self, sql):
                self.sql = sql
                return iter(())

        client = CaptureClient()
        self.assertEqual(
            list(
                iter_news_period(
                    client,
                    "q_live",
                    "2011-01-28",
                    "2011-02-04",
                )
            ),
            [],
        )
        self.assertEqual(client.sql.count("PREWHERE published_date"), 2)
        self.assertIn("FROM `q_live`.`benzinga_news_event_v2` FINAL", client.sql)
        self.assertIn(
            "FROM `q_live`.`benzinga_news_rendered_v2` FINAL",
            client.sql,
        )
        self.assertNotIn(
            "FROM `q_live`.`benzinga_news_event_v2` AS e FINAL",
            client.sql,
        )

    def test_sec_document_query_uses_bounded_latest_rows(self) -> None:
        class CaptureClient:
            sql: list[str] = []

            def iter_json_each_row(self, sql):
                self.sql.append(sql)
                if len(self.sql) == 1:
                    return iter(({
                        "document_id": "doc-1",
                        "cik": "0000000001",
                        "accession_number": "0000000001-26-000001",
                        "filing_id": "filing-1",
                        "sequence_number": 1,
                        "document_type": "EX-99.1",
                        "document_role": "press_release_exhibit",
                        "description": "Press release",
                        "document_name": "release.htm",
                    },))
                return iter(())

        client = CaptureClient()
        self.assertEqual(
            list(
                iter_sec_documents_for_filings(
                    client,
                    "q_live",
                    (("0000000001", "0000000001-26-000001"),),
                    partition=7,
                )
            ),
            [],
        )
        combined = "\n".join(client.sql)
        self.assertNotIn(" FINAL", combined)
        self.assertEqual(
            combined.count("PREWHERE cityHash64(cik) % 64 = 7"),
            2,
        )
        self.assertEqual(
            combined.count(
                "AND (cik, accession_number) IN"
            ),
            1,
        )
        self.assertIn(
            "AND (cik, accession_number, document_id) IN",
            combined,
        )
        self.assertIn(
            "LIMIT 1 BY cik, accession_number, document_id, text_kind",
            combined,
        )
        self.assertIn(
            "LIMIT 1 BY cik, accession_number, sequence_number, document_id",
            combined,
        )

    def test_sec_exact_document_identity_sql_is_byte_bounded(self) -> None:
        identities = tuple(
            (
                f"{index:010d}",
                f"{index:010d}-26-{index:06d}",
                f"{index:064x}",
            )
            for index in range(10_000)
        )
        batches = tuple(_bounded_document_key_sql(identities))
        self.assertGreater(len(batches), 1)
        self.assertEqual(
            sum(batch.count("),(") + 1 for batch in batches),
            len(identities),
        )
        self.assertTrue(all(
            len(batch.encode("utf-8")) <= MAX_EXACT_DOCUMENT_KEY_SQL_BYTES
            for batch in batches
        ))

    def test_bounded_source_is_fully_drained_before_return(self) -> None:
        drained = False

        def rows(*_args):
            nonlocal drained
            yield {"source_id": "one"}
            yield {"source_id": "two"}
            drained = True

        with mock.patch(
            "research.text_intelligence.scoped_labeling_v1.persistence."
            "iter_news_period",
            side_effect=rows,
        ):
            batches = list(
                iter_bounded_source_batches(
                    mock.Mock(),
                    "q_live",
                    "news",
                    "2026-07-01",
                    "2026-07-08",
                )
            )
        self.assertTrue(drained)
        self.assertEqual(len(batches), 1)
        self.assertEqual(
            [row["source_id"] for row in batches[0]],
            ["one", "two"],
        )

    def test_cleanup_converts_only_current_inflight_rows(self) -> None:
        class CleanupClient:
            closed = False

            def iter_json_each_row(self, sql):
                self.sql = sql
                return iter(
                    (
                        {
                            "corpus": "news",
                            "period_start": "2026-07-01",
                            "period_end_exclusive": "2026-07-08",
                            "source_rows": 12,
                            "label_rows": 20,
                            "relation_rows": 40,
                            "worker_pid": 123,
                            "source_seconds": 1.0,
                            "classify_seconds": 2.0,
                            "write_seconds": 3.0,
                            "total_seconds": 6.0,
                        },
                    )
                )

            def close(self):
                self.closed = True

        client = CleanupClient()
        with (
            mock.patch(
                "research.text_intelligence.scoped_labeling_v1.persistence."
                "make_client",
                return_value=client,
            ),
            mock.patch(
                "research.text_intelligence.scoped_labeling_v1.persistence."
                "insert_status",
            ) as insert_status,
        ):
            count = cleanup_inflight_statuses(
                "q_live",
                "run-1",
                "peer failed",
            )
        self.assertEqual(count, 1)
        self.assertTrue(client.closed)
        self.assertIn("run_id='run-1'", client.sql)
        self.assertIn("status IN ('running', 'retrying')", client.sql)
        self.assertEqual(insert_status.call_args.args[9], "interrupted")

    def test_transient_stream_failure_replays_one_bounded_unit(self) -> None:
        class ImmediateResult:
            def __init__(self, value=None, error=None):
                self.value = value
                self.error = error

            def ready(self):
                return True

            def get(self):
                if self.error:
                    raise self.error
                return self.value

        completed = {
            "source_rows": 10,
            "label_rows": 14,
            "relation_rows": 40,
            "source_seconds": 1.0,
            "classify_seconds": 2.0,
            "write_seconds": 0.5,
            "total_seconds": 3.5,
        }

        class RetryPool:
            def __init__(self):
                self.calls = 0

            def apply_async(self, _function, _args):
                self.calls += 1
                if self.calls == 1:
                    return ImmediateResult(
                        error=RuntimeError("IncompleteRead(0 bytes read)")
                    )
                return ImmediateResult(value=completed)

        pool = RetryPool()
        progress_queue = queue.Queue()
        stop_event = mock.Mock()
        stop_event.is_set.return_value = False
        results = execute_bounded_plan(
            pool,
            [("news", "2026-07-01", "2026-07-08")],
            run_id="test-run",
            insert_bytes=1024,
            heartbeat_seconds=30,
            worker_count=1,
            stop_event=stop_event,
            progress_queue=progress_queue,
            completed_before=0,
            total_units=1,
            started_at=time.perf_counter(),
            transient_retries=2,
            # Exercise the scheduler state where the only failed job is
            # waiting for backoff and no asynchronous work remains in flight.
            retry_base_seconds=0.001,
        )
        self.assertEqual(results, [completed])
        self.assertEqual(pool.calls, 2)

    def test_nontransient_unit_failure_is_not_retried(self) -> None:
        class FailedResult:
            def ready(self):
                return True

            def get(self):
                raise RuntimeError("DB::Exception: invalid source contract")

        class FailedPool:
            calls = 0

            def apply_async(self, _function, _args):
                self.calls += 1
                return FailedResult()

        pool = FailedPool()
        stop_event = mock.Mock()
        stop_event.is_set.return_value = False
        with self.assertRaisesRegex(RuntimeError, "invalid source contract"):
            execute_bounded_plan(
                pool,
                [("news", "2026-07-01", "2026-07-08")],
                run_id="test-run",
                insert_bytes=1024,
                heartbeat_seconds=30,
                worker_count=1,
                stop_event=stop_event,
                progress_queue=queue.Queue(),
                completed_before=0,
                total_units=1,
                started_at=time.perf_counter(),
                transient_retries=6,
                retry_base_seconds=0,
            )
        self.assertEqual(pool.calls, 1)

    def test_transient_unit_failure_stops_after_retry_limit(self) -> None:
        class FailedResult:
            def ready(self):
                return True

            def get(self):
                raise RuntimeError("IncompleteRead(0 bytes read)")

        class FailedPool:
            calls = 0

            def apply_async(self, _function, _args):
                self.calls += 1
                return FailedResult()

        pool = FailedPool()
        stop_event = mock.Mock()
        stop_event.is_set.return_value = False
        with self.assertRaisesRegex(RuntimeError, "IncompleteRead"):
            execute_bounded_plan(
                pool,
                [("news", "2026-07-01", "2026-07-08")],
                run_id="test-run",
                insert_bytes=1024,
                heartbeat_seconds=30,
                worker_count=1,
                stop_event=stop_event,
                progress_queue=queue.Queue(),
                completed_before=0,
                total_units=1,
                started_at=time.perf_counter(),
                transient_retries=2,
                retry_base_seconds=0,
            )
        self.assertEqual(pool.calls, 3)

    def test_transient_classifier_rejects_clickhouse_data_errors(self) -> None:
        self.assertTrue(
            is_transient_clickhouse_error(
                RuntimeError("IncompleteRead(0 bytes read)")
            )
        )
        self.assertFalse(
            is_transient_clickhouse_error(
                RuntimeError("DB::Exception: unknown column")
            )
        )
        self.assertTrue(
            is_transient_clickhouse_error(
                RuntimeError("ClickHouse HTTP 503 Service Unavailable")
            )
        )
        self.assertFalse(
            is_transient_clickhouse_error(
                RuntimeError(
                    "ClickHouse HTTP 503: DB::Exception: unknown column"
                )
            )
        )

    def test_process_worker_initializer_shares_stop_state(self) -> None:
        from .persistence import initialize_worker

        context = multiprocessing.get_context("spawn")
        stop_event = context.Event()
        progress_queue = context.Queue()
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=1,
            mp_context=context,
            initializer=initialize_worker,
            initargs=("test_database", stop_event, progress_queue),
        ) as executor:
            pid, database, stopped = executor.submit(
                process_worker_probe
            ).result(timeout=20)
            self.assertNotEqual(pid, os.getpid())
            self.assertEqual(database, "test_database")
            self.assertFalse(stopped)
            stop_event.set()
            _, _, stopped = executor.submit(
                process_worker_probe
            ).result(timeout=20)
            self.assertTrue(stopped)
        progress = progress_queue.get(timeout=5)
        self.assertEqual(progress["source_rows"], 1)
        progress_queue.close()

    def test_insert_buffer_flushes_on_serialized_bytes(self) -> None:
        client = mock.Mock()
        buffer = JsonInsertBuffer(
            client=client,
            database="q_live",
            table="labels",
            max_bytes=40,
            max_rows=100,
        )
        buffer.add({"text": "a" * 24})
        buffer.add({"text": "b" * 24})
        buffer.flush()
        self.assertEqual(client.execute.call_count, 2)
        for call in client.execute.call_args_list:
            self.assertIn("FORMAT JSONEachRow", call.args[0])

    def test_insert_buffer_rejects_one_oversized_row_before_transport(
        self,
    ) -> None:
        client = mock.Mock()
        buffer = JsonInsertBuffer(
            client=client,
            database="q_live",
            table="labels",
            max_bytes=1_000,
            max_row_bytes=100,
        )
        with self.assertRaisesRegex(
            PermanentPayloadError,
            "news / source-1 / unit-1 / EXMP",
        ):
            buffer.add({
                "corpus": "news",
                "source_id": "source-1",
                "unit_id": "unit-1",
                "ticker": "EXMP",
                "text": "x" * 200,
            })
        client.execute.assert_not_called()
        self.assertFalse(
            is_transient_clickhouse_error(
                PermanentPayloadError("oversized compact label")
            )
        )

    def test_compact_semantic_payload_excludes_repeated_source_text(
        self,
    ) -> None:
        compact = compact_semantic_payload({
            "authority_version": "authority-v1",
            "source_id": "unit-1",
            "corpus": "sec",
            "blocks": [{
                "text": "source text that must not be persisted",
                "kind": "paragraph",
            }],
            "spans": [
                {
                    "span_type": "financial",
                    "subtype": "money",
                    "raw": "$25 million",
                    "normalized": "25000000",
                    "unit": "USD",
                    "context": "repeated audit context",
                },
                {
                    "span_type": "financial",
                    "subtype": "money",
                    "raw": "$25 million",
                    "normalized": "25000000",
                    "unit": "USD",
                    "context": "repeated audit context",
                },
            ],
            "normalized_semantic_text": "another complete text copy",
            "labels": [],
            "content_role": "primary_event",
            "origin": "regulatory_primary",
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "modality": "confirmed",
            "time_orientation": "current",
            "keywords": ["offering"],
            "candidates": [],
            "quality_flags": [],
        })
        encoded = json.dumps(compact)
        self.assertNotIn("source text that must not be persisted", encoded)
        self.assertNotIn("another complete text copy", encoded)
        self.assertNotIn("repeated audit context", encoded)
        self.assertEqual(
            compact["normalized_values"],
            [{
                "span_type": "financial",
                "subtype": "money",
                "normalized": "25000000",
                "unit": "USD",
                "count": 2,
            }],
        )
        self.assertEqual(compact["audit_counts"]["typed_spans"], 2)

    def test_sec_mapping_is_point_in_time_and_confidence_ordered(self) -> None:
        row = {
            "cik": "0001",
            "source_timestamp": "2026-07-10T12:00:00Z",
        }
        mappings = {
            "0001": [
                {
                    "ticker": "OLD",
                    "valid_from_date": "2020-01-01",
                    "valid_to_date_exclusive": "2025-01-01",
                    "mapping_status": "resolved",
                    "ambiguity_status": "",
                    "confidence_score": 1.0,
                },
                {
                    "ticker": "LOW",
                    "valid_from_date": "2025-01-01",
                    "valid_to_date_exclusive": "",
                    "mapping_status": "resolved",
                    "ambiguity_status": "",
                    "confidence_score": 0.5,
                },
                {
                    "ticker": "HIGH",
                    "valid_from_date": "2025-01-01",
                    "valid_to_date_exclusive": "",
                    "mapping_status": "active",
                    "ambiguity_status": "",
                    "confidence_score": 0.9,
                },
            ]
        }
        attach_sec_ticker(row, mappings)
        self.assertEqual(row["tickers"], ["HIGH"])
        self.assertEqual(
            row["ticker_mapping_status"],
            "resolved_point_in_time",
        )

    def test_persistence_requires_matching_clean_certification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "labeling_version": SCOPED_LABELING_VERSION,
                        "news_audits": 5,
                        "sec_audits": 5,
                        "review_attention": 0,
                        "missing_news_scope_cases": [],
                        "expected_outcome_failures": [],
                    }
                ),
                encoding="utf-8",
            )
            assert_certification(path)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                assert_certification(path)

    def test_roundup_creates_ticker_specific_observations(self) -> None:
        text = """Title: 42 Stocks Moving in Wednesday's Pre-Market Session
Body:
- Cancer Genetics, Inc. (NASDAQ:CGIX) shares rose 200.7% to $17.35 in pre-market trading after reporting a $10 million private placement.
- Other Corp (NYSE:OTHR) shares fell 12.5% to $4.20 after lowering guidance.
"""
        units = extract_news_units(
            source_id="19582725",
            title="42 Stocks Moving in Wednesday's Pre-Market Session",
            text=text,
            tickers=("CGIX", "OTHR"),
        )
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].tickers, ("CGIX",))
        self.assertEqual(units[0].observed_reaction.direction, "up")
        self.assertEqual(units[0].observed_reaction.move_pct, 200.7)
        self.assertEqual(units[0].observed_reaction.resulting_price, 17.35)
        self.assertEqual(
            units[0].reported_catalyst,
            "reporting a $10 million private placement",
        )

    def test_roundup_context_can_never_be_reaction_target(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="news-roundup",
            timestamp="2026-07-28T12:00:00Z",
            title="50 Biggest Movers From Friday",
            text=(
                "Body:\n- Example Corp (NASDAQ:EXMP) shares rose 25% "
                "to $5.00 after receiving FDA approval."
            ),
            tickers=("EXMP",),
            metadata={"channels": ["Movers"], "author": "Benzinga"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 1)
        self.assertFalse(labels[0].forecast_trigger_eligible)
        self.assertFalse(labels[0].reaction_evaluation_eligible)
        self.assertTrue(labels[0].issuer_history_context_eligible)
        self.assertIn(
            "regulatory.fda_approval",
            {
                f"{item['family']}.{item['subtype']}"
                for item in labels[0].semantic["labels"]
            },
        )

    def test_reported_reactions_are_ticker_specific_and_accept_is_up(self) -> None:
        text = (
            "Price Action: AERI stock is up 35.6% at $15.13, "
            "and ALC shares are up 0.09% at $68.00."
        )
        aeri = extract_observed_reaction(text, ticker="AERI")
        alc = extract_observed_reaction(text, ticker="ALC")
        missing = extract_observed_reaction(text, ticker="OTHER")
        self.assertEqual(
            (aeri.direction, aeri.move_pct, aeri.resulting_price),
            ("up", 35.6, 15.13),
        )
        self.assertEqual(
            (alc.direction, alc.move_pct, alc.resulting_price),
            ("up", 0.09, 68.0),
        )
        self.assertEqual(missing.direction, "")

    def test_reported_reactions_accept_increased_decreased_and_trading_lower(
        self,
    ) -> None:
        cases = (
            (
                "Example stock increased by 15.61% to $3.85.",
                "up",
                15.61,
            ),
            (
                "Example shares decreased by 3.91% to $5.82.",
                "down",
                3.91,
            ),
            (
                "Example shares are trading lower by 1.11% at $617.35.",
                "down",
                1.11,
            ),
        )
        for text, direction, move in cases:
            reaction = extract_observed_reaction(text)
            self.assertEqual(reaction.direction, direction)
            self.assertEqual(reaction.move_pct, move)

    def test_provider_link_disambiguates_single_word_alias_collision(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("VERX", "issuer-verx", ("Vertex",)),
            IssuerIdentity(
                "VRTX",
                "issuer-vrtx",
                ("Vertex", "Vertex Pharmaceuticals Incorporated"),
            ),
        ))
        analysis = analyze_news_scope(
            source_id="vertex-hold",
            title="Vertex Announces Clinical Hold",
            text=(
                "Title: Vertex Announces Clinical Hold\n"
                "Vertex Pharmaceuticals Incorporated (NASDAQ:VRTX) "
                "announced its study was placed on clinical hold."
            ),
            tickers=("VRTX",),
            timestamp="2022-05-02T12:35:25Z",
            issuer_resolver=resolver,
        )
        self.assertEqual(analysis.resolved_subjects, ("VRTX",))
        self.assertEqual({unit.tickers for unit in analysis.units}, {("VRTX",)})

    def test_identity_authority_fills_missing_historical_listing_from_entity_table(
        self,
    ) -> None:
        canonical = (
            {
                "ticker": "URI",
                "issuer_id": "issuer:cik:0001067701",
                "issuer_name": "UNITED RENTALS, INC.",
                "legal_name": "United Rentals, Inc.",
                "exchange_code": "XNYS",
                "list_date": "1997-12-18",
                "delisted_date": "",
                "cik": "0001067701",
                "source_authority": "canonical_identity_graph",
            },
        )
        fallback = (
            {
                "ticker": "URI",
                "issuer_id": "issuer:cik:0001067701",
                "issuer_name": "United Rentals, Inc.",
                "exchange_code": "XNYS",
                "delisted_date": "",
                "source_authority": "market_ticker_event_entity",
            },
            {
                "ticker": "HEES",
                "issuer_id": "issuer:cik:0001339605",
                "issuer_name": "H&E Equipment Services, Inc.",
                "exchange_code": "XNAS",
                "delisted_date": "2025-06-03",
                "cik": "0001339605",
                "source_authority": "market_ticker_event_entity",
            },
        )
        resolver = NewsIssuerResolver(_identity_rows(canonical, fallback))
        snapshot = resolver.reference_snapshot(
            ("URI", "HEES"), timestamp="2025-02-18T12:19:44Z"
        )
        self.assertEqual(len(snapshot), 2)
        hees = next(row for row in snapshot if row["ticker"] == "HEES")
        self.assertEqual(hees["cik"], "0001339605")
        self.assertEqual(hees["source_authority"], "market_ticker_event_entity")
        self.assertFalse(
            resolver.reference_snapshot(
                ("HEES",), timestamp="2025-06-04T12:19:44Z"
            )
        )

        analysis = analyze_news_scope(
            source_id="united-rentals-withdraws",
            title=(
                "United Rentals To No Longer Pursue Acquisition Of "
                "H&E Equipment Services"
            ),
            text=(
                "H&E Equipment Services is required to pay a termination fee "
                "to United Rentals if it enters another acquisition agreement."
            ),
            tickers=("HEES", "HRI", "URI"),
            timestamp="2025-02-18T12:19:44Z",
            issuer_resolver=resolver,
        )
        self.assertEqual(set(analysis.resolved_subjects), {"HEES", "URI"})

    def test_full_article_name_introduces_bounded_ampersand_shorthand(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("HEES", "issuer:hees", ("H&E Equipment Services",)),
            IssuerIdentity("URI", "issuer:uri", ("United Rentals",)),
        ))
        text = (
            "United Rentals will no longer pursue H&E Equipment Services. "
            "H&E may enter into a competing acquisition proposal."
        )
        local = resolver.with_article_identities(text)
        matches = local.resolve(
            "H&E may enter into a competing acquisition proposal.",
            linked_tickers=("HEES", "URI"),
        )
        self.assertEqual(tuple(match.ticker for match in matches), ("HEES",))
        self.assertIn("issuer_alias:h e", matches[0].evidence)
        self.assertFalse(resolver.resolve("H&E may enter another proposal."))

    def test_ampersand_shorthand_requires_full_name_in_same_article(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("ABCO", "issuer:abco", ("A&B Holdings",)),
            IssuerIdentity("ABIO", "issuer:abio", ("A&B Industries",)),
        ))
        local = resolver.with_article_identities(
            "A&B Holdings announced a review. A&B later issued a statement."
        )
        matches = local.resolve(
            "A&B later issued a statement.",
            linked_tickers=("ABCO", "ABIO"),
        )
        self.assertEqual(tuple(match.ticker for match in matches), ("ABCO",))

    def test_unlinked_common_single_word_alias_cannot_create_issuer(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("MAJI", "issuer-maji", ("Marijuana",)),
        ))
        analysis = analyze_news_scope(
            source_id="policy",
            title="States revisit marijuana policy",
            text="Body: Several states changed marijuana regulations.",
            tickers=(),
            timestamp="2022-11-23T14:28:52Z",
            issuer_resolver=resolver,
        )
        self.assertFalse(analysis.resolved_subjects)
        self.assertFalse(analysis.units)

    def test_related_article_links_cannot_introduce_issuer_or_event(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity(
                "PH",
                "issuer-ph",
                ("Parker-Hannifin", "Parker-Hannifin Corp"),
            ),
            IssuerIdentity("ETN", "issuer-etn", ("Eaton",)),
            IssuerIdentity("BLK", "issuer-blk", ("BlackRock",)),
        ))
        document = SemanticDocument(
            corpus="news",
            source_id="earnings-related-links",
            timestamp="2024-10-31T15:22:10Z",
            title="Parker-Hannifin reports earnings",
            text=(
                "Parker-Hannifin Corp (NYSE:PH) beat the consensus and "
                "raised its adjusted EPS guidance.\n"
                "See Also: Eaton Q3 Earnings: Raised Guidance\n"
                "Now Read:\n"
                "- BlackRock Bitcoin ETF update"
            ),
            tickers=("PH",),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=resolver,
        )
        self.assertEqual({label.ticker for label in labels}, {"PH"})
        self.assertIn(
            "guidance.raise",
            labels[0].classification["event_concepts"],
        )

    def test_multi_ticker_unscoped_prose_is_not_assigned(self) -> None:
        units = extract_news_units(
            source_id="multi",
            title="Technology shares move",
            text="Body: Several technology companies moved in active trading.",
            tickers=("AAAA", "BBBB"),
        )
        self.assertFalse(units)

    def test_single_ticker_article_is_one_document_unit(self) -> None:
        units = extract_news_units(
            source_id="single",
            title="Example raises guidance",
            text=(
                "Body: Example announced that it raised guidance.\n"
                "Revenue also increased year over year."
            ),
            tickers=("EXMP",),
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(len(units), 1)
        self.assertIn("Revenue also increased", units[0].text)
        self.assertEqual(units[0].role, "primary_or_editorial_document")

    def test_corporate_guidance_upgrade_is_not_an_analyst_action(self) -> None:
        units = extract_news_units(
            source_id="guidance-upgrade",
            title="Example Therapeutics upgrades guidance",
            text=(
                "Body: Example Therapeutics upgraded its revenue guidance "
                "after stronger demand."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].role, "primary_or_editorial_document")

    def test_single_provider_link_does_not_hide_mixed_issuer_article(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="single-link-mixed",
            timestamp="2026-07-28T12:00:00Z",
            title="Example Therapeutics and Other Corp report updates",
            text=(
                "Body: Example Therapeutics announced that it raised guidance. "
                "Other Corp (NYSE:OTHR) announced a registered direct offering."
            ),
            tickers=("EXMP",),
            metadata={"author": "Editorial Desk"},
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual({label.ticker for label in labels}, {"EXMP", "OTHR"})
        self.assertTrue(all(label.forecast_trigger_eligible for label in labels))
        self.assertEqual(
            {label.unit_role for label in labels},
            {"issuer_event_document"},
        )

    def test_analyst_firm_is_not_treated_as_action_target(self) -> None:
        analysis = analyze_news_scope(
            source_id="analyst-target",
            title="Goldman Sachs upgrades Apple",
            text=(
                "Body: Goldman Sachs upgraded Apple Inc. to Buy and raised "
                "its price target to $250."
            ),
            tickers=("AAPL",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.resolved_subjects, ("AAPL",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertEqual(analysis.units[0].tickers, ("AAPL",))
        self.assertEqual(analysis.units[0].role, "analyst_opinion")

    def test_exchange_prefixed_provider_ticker_is_normalized(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("CRES", "cres", ("Crest Resources Inc.",)),
        ))
        analysis = analyze_news_scope(
            source_id="exchange-prefixed-provider-ticker",
            title="Crest Resources reports an acquisition",
            text=(
                "Crest Resources Inc. (CSE:CRES) announced that it acquired "
                "common shares of another issuer."
            ),
            tickers=("CSE:CRES",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=resolver,
        )
        self.assertEqual(analysis.linked_tickers, ("CRES",))
        self.assertEqual({unit.tickers for unit in analysis.units}, {("CRES",)})

    def test_automated_earnings_summary_is_context_not_trigger(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="automated-earnings-summary",
            timestamp="2026-07-28T12:00:00Z",
            title="Earnings Outlook For Example Therapeutics",
            text=(
                "Example Therapeutics (NASDAQ:EXMP) is set to give its latest "
                "quarterly earnings report. Analysts estimate EPS of $1.20. "
                "This article was generated by Benzinga's automated content "
                "engine and reviewed by an editor."
            ),
            tickers=("EXMP",),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertTrue(labels)
        self.assertTrue(all(
            label.classification["content_role"] == "automated_summary"
            for label in labels
        ))
        self.assertTrue(all(
            label.unit_role == "ticker_scoped_editorial_context"
            for label in labels
        ))
        self.assertTrue(all(
            not label.forecast_trigger_eligible
            and not label.reaction_evaluation_eligible
            for label in labels
        ))

    def test_regulatory_clearance_is_not_a_negative_investigation(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="investigation-clearance",
            timestamp="2026-07-28T12:00:00Z",
            title="Example avoids investigation into acquisition",
            text=(
                "Example Therapeutics (NASDAQ:EXMP) will not face a formal "
                "investigation. The regulator found no risk of such an outcome."
            ),
            tickers=("EXMP",),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertTrue(labels)
        concepts = set(labels[0].classification["event_concepts"])
        self.assertIn("legal.investigation_clearance", concepts)
        self.assertNotIn("legal.investigation", concepts)
        self.assertEqual(
            labels[0].classification["semantic_direction"],
            "positive",
        )

    def test_moved_upwards_reaction_and_after_market_session_are_parsed(self) -> None:
        reaction = extract_observed_reaction(
            "Example shares moved upwards by 5.13% to $6.96 during "
            "Tuesday's after-market session.",
            ticker="EXMP",
        )
        self.assertEqual(reaction.direction, "up")
        self.assertEqual(reaction.move_pct, 5.13)
        self.assertEqual(reaction.resulting_price, 6.96)
        self.assertEqual(reaction.market_session, "after_market")

    def test_exchange_scoped_reaction_without_shares_word_is_parsed(self) -> None:
        reaction = extract_observed_reaction(
            "United States Antimony Corporation (NYSE:UAMY) rose 74.4% "
            "to $1.70 in pre-market trading.",
            ticker="UAMY",
        )
        self.assertEqual(reaction.direction, "up")
        self.assertEqual(reaction.move_pct, 74.4)
        self.assertEqual(reaction.resulting_price, 1.70)
        self.assertEqual(reaction.market_session, "pre_market")

    def test_unresolved_company_like_passage_does_not_inherit_single_link(self) -> None:
        analysis = analyze_news_scope(
            source_id="unresolved-peer",
            title="Example Therapeutics reports an update",
            text=(
                "Body: Example Therapeutics raised guidance. "
                "Mystery Holdings Corp. announced a separate offering."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(
            analysis.document_decision,
            "unresolved_issuer_passage_abstention",
        )
        self.assertEqual(len(analysis.units), 1)
        self.assertEqual(analysis.units[0].tickers, ("EXMP",))
        self.assertIn("Mystery Holdings", analysis.units[0].text)
        self.assertNotIn(
            "Mystery Holdings", analysis.units[0].semantic_text
        )
        unresolved = [
            passage
            for passage in analysis.passages
            if passage.decision == "abstained_unresolved_company_mention"
        ]
        self.assertEqual(len(unresolved), 1)

    def test_single_link_without_text_resolved_subject_abstains(self) -> None:
        analysis = analyze_news_scope(
            source_id="metadata-only",
            title="Quarterly update",
            text="Body: The company discussed general market conditions.",
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.document_decision, "abstained_no_resolved_issuer")
        self.assertFalse(analysis.units)

    def test_article_local_exchange_pair_resolves_historical_issuer_name(self) -> None:
        resolver = NewsIssuerResolver(())
        analysis = analyze_news_scope(
            source_id="historical-name",
            title="Salarius Pharmaceuticals reports FDA update",
            text=(
                "Body: Salarius Pharmaceuticals, Inc. (NASDAQ:SLRX) "
                "announced that the FDA removed its partial clinical hold."
            ),
            tickers=("SLRX",),
            timestamp="2023-05-09T12:02:54Z",
            issuer_resolver=resolver,
        )
        self.assertEqual(analysis.resolved_subjects, ("SLRX",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")

    def test_unresolved_counterparty_does_not_erase_known_issuer_event(self) -> None:
        analysis = analyze_news_scope(
            source_id="spac-termination",
            title="Pine Technology Acquisition Corp. terminates merger",
            text=(
                "Body: Pine Technology Acquisition Corp. "
                "(NASDAQ:PTOC, PTOCW, PTOCU) and The Tomorrow Companies Inc. "
                "agreed to terminate their merger agreement."
            ),
            tickers=("PTOC",),
            timestamp="2022-03-07T12:11:37Z",
            issuer_resolver=NewsIssuerResolver(()),
        )
        self.assertEqual(
            analysis.document_decision,
            "unresolved_issuer_passage_abstention",
        )
        self.assertEqual(len(analysis.units), 1)
        self.assertEqual(analysis.units[0].tickers, ("PTOC",))
        self.assertEqual(analysis.units[0].evidence_scope, "shared_ambiguous")
        self.assertTrue(any(
            passage.decision
            == "assigned_known_issuer_with_unresolved_counterparty"
            for passage in analysis.passages
        ))

    def test_external_enrichment_cannot_change_publication_time_subject(self) -> None:
        analysis = analyze_news_scope(
            source_id="external-enrichment",
            title="Example Therapeutics raises guidance",
            text=(
                "Title: Example Therapeutics raises guidance\n"
                "Source [provider_body:0] https://provider.test/article\n"
                "Example Therapeutics, Inc. (NASDAQ:EXMP) raised guidance.\n"
                "Source [external:1]\n"
                "Example Therapeutics collaborates with Other Corp and Apple Inc."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.resolved_subjects, ("EXMP",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertNotIn("Other Corp", analysis.units[0].text)
        self.assertNotIn("Apple Inc", analysis.units[0].text)
        self.assertTrue(any(
            passage.decision == "abstained_external_enrichment"
            for passage in analysis.passages
        ))

    def test_multi_issuer_acquisition_keeps_full_text_and_scopes_labels(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("ALC", "alcon", ("Alcon AG", "Alcon")),
            IssuerIdentity(
                "AERI",
                "aerie",
                ("Aerie Pharmaceuticals Inc", "Aerie Pharmaceuticals", "Aerie"),
            ),
        ))
        document = SemanticDocument(
            corpus="news",
            source_id="acquisition-analyst",
            timestamp="2022-08-23T19:26:34Z",
            title=(
                "Alcon May Struggle To Meet Margin Targets With This "
                "Latest Acquisition, Says This Analyst"
            ),
            text=(
                "Source [provider_body:0]\n"
                "Alcon AG (NYSE:ALC) agreed to acquire Aerie Pharmaceuticals "
                "Inc (NASDAQ:AERI) for $770 million.\n"
                "The deal could make it more difficult for ALC to reach its "
                "operating margin targets and be dilutive to operating margin.\n"
                "Needham downgraded AERI to Hold from Buy."
            ),
            tickers=("AERI", "ALC"),
            metadata={"author": "Benzinga Analyst Ratings"},
        )
        labels = classify_news_document(document, issuer_resolver=resolver)
        self.assertEqual({item.ticker for item in labels}, {"AERI", "ALC"})
        self.assertTrue(all(item.forecast_trigger_eligible for item in labels))
        self.assertEqual(
            {item.ticker: item.issuer_role for item in labels},
            {"ALC": "acquirer", "AERI": "target"},
        )
        self.assertEqual(
            len({item.publication_text_hash for item in labels}),
            1,
        )
        concepts = {
            item.ticker: set(item.classification["event_concepts"])
            for item in labels
        }
        self.assertIn("ma_transaction.acquisition", concepts["ALC"])
        self.assertIn("ma_transaction.acquisition", concepts["AERI"])
        self.assertIn("profitability.margin_pressure", concepts["ALC"])
        self.assertNotIn("profitability.margin_pressure", concepts["AERI"])
        self.assertIn("analyst_action.downgrade", concepts["AERI"])
        by_ticker = {item.ticker: item for item in labels}
        self.assertEqual(
            by_ticker["AERI"].classification["semantic_direction"],
            "positive",
        )
        self.assertEqual(
            by_ticker["ALC"].classification["semantic_direction"],
            "mixed",
        )
        self.assertEqual(
            by_ticker["AERI"].classification["content_role"],
            "analyst_event",
        )
        self.assertEqual(
            by_ticker["AERI"].classification["issuer_relationship"],
            "analyst_opinion",
        )
        self.assertEqual(
            by_ticker["AERI"].classification[
                "semantic_score_adjustment"
            ],
            0.8,
        )

    def test_relational_lead_owns_subjectless_followup_predicate(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("HEES", "hees", ("H&E Equipment Services",)),
            IssuerIdentity("URI", "uri", ("United Rentals",)),
        ))
        variants = (
            (
                "United Rentals To No Longer Pursue Acquisition Of H&E "
                "Equipment Services; Plans To Restart Its Share Repurchase Program"
            ),
            (
                "United Rentals To No Longer Pursue Acquisition Of H&E "
                "Equipment Services And Plans To Restart Its Share Repurchase Program"
            ),
        )
        for title in variants:
            with self.subTest(title=title):
                analysis = analyze_news_scope(
                    source_id="clause-attribution",
                    title=title,
                    text=f"Title: {title}",
                    tickers=("HEES", "URI"),
                    timestamp="2025-02-18T12:19:44Z",
                    issuer_resolver=resolver,
                )
                units = {unit.tickers[0]: unit for unit in analysis.units}
                self.assertEqual(set(units), {"HEES", "URI"})
                self.assertNotIn(
                    "share repurchase", units["HEES"].semantic_text.casefold()
                )
                self.assertIn(
                    "share repurchase", units["URI"].semantic_text.casefold()
                )
                self.assertEqual(units["HEES"].issuer_role, "target")
                self.assertEqual(units["URI"].issuer_role, "acquirer")
                self.assertTrue(any(
                    passage.decision == "assigned_relational_subject_continuation"
                    and passage.assigned_ticker == "URI"
                    for passage in analysis.passages
                ))

    def test_unresolved_background_does_not_disable_resolved_event(
        self,
    ) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="scope-gate",
            timestamp="2026-07-14T12:00:00Z",
            title="Example Therapeutics Announces Positive Trial Results",
            text=(
                "Example Therapeutics, Inc. (NASDAQ:EXMP) announced positive "
                "Phase 3 trial results.\n"
                "Unresolved Biopharma Inc. will participate in the program."
            ),
            tickers=("EXMP",),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertTrue(labels)
        self.assertTrue(all(
            label.forecast_trigger_eligible
            and label.reaction_evaluation_eligible
            for label in labels
        ))
        self.assertTrue(all(
            "event_scoped_eligibility_v4"
            in label.classification["quality_flags"]
            for label in labels
        ))

    def test_multiple_provider_symbols_for_one_issuer_remain_trigger_safe(
        self,
    ) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="same-issuer-symbols",
            timestamp="2026-07-14T12:00:00Z",
            title="Example Therapeutics Announces Positive Trial Results",
            text=(
                "Example Therapeutics, Inc. (NASDAQ:EXMP) announced that its "
                "Phase 3 trial met the primary endpoint."
            ),
            tickers=("EXMP", "EXM.A"),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        analysis = analyze_news_scope(
            source_id=document.source_id,
            title=document.title,
            text=document.text,
            tickers=document.tickers,
            timestamp=document.timestamp,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertEqual(len(labels), 1)
        self.assertNotIn(
            "document_issuer_scope_not_trigger_safe",
            labels[0].classification["quality_flags"],
        )

    def test_multi_ticker_independent_events_are_each_trigger_eligible(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="multi-scoped",
            timestamp="2026-07-28T12:00:00Z",
            title="Two healthcare companies report updates",
            text=(
                "Example Corp (NASDAQ:EXMP) received FDA approval.\n"
                "Other Corp (NYSE:OTHR) announced a public offering."
            ),
            tickers=("EXMP", "OTHR"),
            metadata={"author": "Editorial Desk"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 2)
        self.assertTrue(all(item.forecast_trigger_eligible for item in labels))
        self.assertTrue(all(item.issuer_history_context_eligible for item in labels))

    def test_sec_extractor_ignores_signature_and_keeps_event(self) -> None:
        text = """ITEM 1.01
The registrant entered into a registered direct offering for $25 million.
SIGNATURES
Pursuant to the requirements of the Securities Exchange Act, the registrant signed this report.
"""
        units = extract_sec_units(
            source_id="sec-1",
            title="8-K",
            text=text,
            ticker="EXMP",
            metadata={"document_role": "primary_document"},
        )
        self.assertEqual(len(units), 1)
        self.assertIn("registered direct offering", units[0].text)
        self.assertNotIn("signed this report", units[0].text)

    def test_sec_extractor_excludes_asset_level_xml_by_taxonomy(self) -> None:
        units = extract_sec_units(
            source_id="asset-data",
            title="EX-102",
            text=(
                "<assetData>originalLoanAmount=42814.75; "
                "originalInterestRatePercentage=0.0599</assetData>"
            ),
            ticker="",
            metadata={
                "document_type": "EX-102",
                "document_role": "material_exhibit",
                "description": "Asset-level data file",
            },
        )
        self.assertEqual(units, ())

    def test_sec_extractor_splits_large_narrative_evidence(self) -> None:
        text = (
            "ITEM 1.01\n"
            + "The company entered into a financing agreement. "
            + ("Material commercial terms and obligations apply. " * 2_000)
        )
        units = extract_sec_units(
            source_id="large-contract",
            title="8-K",
            text=text,
            ticker="EXMP",
            metadata={
                "document_type": "8-K",
                "document_role": "primary_document",
            },
        )
        self.assertGreater(len(units), 1)
        self.assertTrue(
            all(len(unit.semantic_text) <= 32_000 for unit in units)
        )
        self.assertEqual(
            sum(unit.semantic_text.count("Material") for unit in units),
            2_000,
        )

    def test_sec_labels_only_relevant_units(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-2",
            timestamp="2026-07-28T12:00:00Z",
            title="Example 8-K EX-99.1",
            text=(
                "BUSINESS UPDATE\n"
                "The company announced a registered direct offering.\n"
                "FORWARD-LOOKING STATEMENTS\n"
                "These statements involve risks and uncertainties."
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "8-K",
                "document_type": "EX-99.1",
                "document_role": "press_release_exhibit",
                "text_kind": "press_release_exhibit",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertEqual(len(labels), 1)
        self.assertIn(
            "financing.registered_direct",
            labels[0].classification["event_concepts"],
        )

    def test_sec_event_concepts_cover_certified_missing_cases(self) -> None:
        cases = (
            (
                "The company reached settlement with Mylan.",
                "legal.settlement",
            ),
            (
                "The Company maintains an Employee Share Purchase Plan "
                "and desires to amend the Plan.",
                "management_governance.employee_share_purchase_plan_amendment",
            ),
            (
                "The company entered a securities purchase agreement "
                "for shares of preferred stock and purchase warrants.",
                "financing.preferred_stock_private_placement",
            ),
        )
        for index, (text, concept) in enumerate(cases):
            document = SemanticDocument(
                corpus="sec",
                source_id=f"sec-concept-{index}",
                timestamp="2026-07-28T12:00:00Z",
                title="Example 8-K exhibit",
                text=text,
                tickers=("EXMP",),
                metadata={
                    "form_type": "8-K",
                    "document_type": "EX-99.1",
                    "document_role": "press_release_exhibit",
                    "text_kind": "press_release_exhibit",
                },
            )
            labels = classify_sec_document(document)
            self.assertIn(
                concept,
                {
                    value
                    for label in labels
                    for value in label.classification["event_concepts"]
                },
            )

    def test_relationship_rows_normalize_graph_without_publication_text(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("ALC", "alcon", ("Alcon AG", "Alcon")),
            IssuerIdentity("AERI", "aerie", ("Aerie Pharmaceuticals",)),
        ))
        document = SemanticDocument(
            corpus="news",
            source_id="graph-acquisition",
            timestamp="2022-08-23T19:26:34Z",
            title="Alcon acquisition",
            text=(
                "Alcon AG (NYSE:ALC) agreed to acquire "
                "Aerie Pharmaceuticals (NASDAQ:AERI)."
            ),
            tickers=("ALC", "AERI"),
        )
        labels = classify_news_document(document, issuer_resolver=resolver)
        relations = [
            row for label in labels
            for row in relationship_rows(document, label, "test-run")
        ]
        self.assertTrue(any(
            row["relation_type"] == "affects_issuer"
            and row["relation_role"] == "acquirer"
            for row in relations
        ))
        self.assertTrue(any(
            row["relation_type"] == "affects_issuer"
            and row["relation_role"] == "target"
            for row in relations
        ))
        self.assertTrue(all("text" not in row for row in relations))

    def test_generic_purchase_order_disclosure_is_not_contract_award(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-background",
            timestamp="2026-07-28T12:00:00Z",
            title="Annual report",
            text="Purchase orders are used in the ordinary course of business.",
            tickers=("EXMP",),
            metadata={
                "form_type": "10-K",
                "document_type": "10-K",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        concepts = {
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        }
        self.assertNotIn("contract_order.award", concepts)

    def test_form_four_exercise_price_is_not_financing_event(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="form-4",
            timestamp="2026-07-28T12:00:00Z",
            title="Example Form 4",
            text=(
                "Common Stock\nTransaction code M\n"
                "Warrant conversion or exercise price $2.50\n"
                "Performance Shares"
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "4",
                "document_type": "4",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertTrue(labels)
        self.assertTrue(all(
            item.classification["content_role"] == "ownership_transaction"
            for item in labels
        ))
        self.assertTrue(all(not item.forecast_trigger_eligible for item in labels))
        self.assertNotIn(
            "financing.warrant",
            {
                concept
                for item in labels
                for concept in item.classification["event_concepts"]
            },
        )


if __name__ == "__main__":
    unittest.main()
