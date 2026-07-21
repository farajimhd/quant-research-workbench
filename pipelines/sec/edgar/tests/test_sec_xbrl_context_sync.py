from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import SecWriteResult
from pipelines.sec.edgar.sec_pipeline.feed import SecFeedItem
from pipelines.sec.edgar.sec_pipeline.xbrl_context import (
    SecXbrlContextSync,
    XbrlContextSyncConfig,
    XbrlContextSyncResult,
    context_identities_sql,
    mapped_filing_rows_sql,
    source_company_fact_rows_sql,
    source_frame_observation_rows_sql,
)
from research.mlops.packed_market.context import DEFAULTS, PackedContextConfig
from services.gateway_core.dashboard import configured_tables
from services.gateway_core.rich_renderer import metric_label
from services.sec_gateway.gateway import SecGateway


class SecXbrlContextSyncTests(unittest.TestCase):
    def test_mapped_filing_uses_event_valid_bridge_from_reference_database(self) -> None:
        config = XbrlContextSyncConfig(source_database="q_sec_tmp", bridge_database="q_live")

        sql = mapped_filing_rows_sql(config, cik="0000000123", accession_number="0000000123-26-000001")

        self.assertIn("`q_sec_tmp`.`sec_filing_v3`", sql)
        self.assertIn("`q_live`.`id_sec_market_bridge_v3`", sql)
        self.assertIn("b.valid_from_date <= toDate(f.accepted_at_utc)", sql)
        self.assertIn("b.valid_to_date_exclusive > toDate(f.accepted_at_utc)", sql)
        self.assertIn("f.accepted_at_utc IS NOT NULL", sql)
        self.assertIn("formatDateTime(f.accepted_at_utc", sql)
        self.assertIn("'UTC') AS accepted_at_utc", sql)

    def test_recovery_reads_accession_rows_without_historical_join(self) -> None:
        config = XbrlContextSyncConfig()
        facts_sql = source_company_fact_rows_sql(
            config,
            cik="0000000123",
            accession_number="0000000123-26-000001",
        )
        frames_sql = source_frame_observation_rows_sql(
            config,
            cik="0000000123",
            accession_number="0000000123-26-000001",
        )

        self.assertIn("`q_live`.`sec_xbrl_company_fact_v3`", facts_sql)
        self.assertIn("`q_live`.`sec_xbrl_frame_observation_v3`", frames_sql)
        self.assertNotIn(" JOIN ", facts_sql)
        self.assertNotIn(" JOIN ", frames_sql)

    def test_target_identity_lookup_uses_ordered_v3_key_prefix(self) -> None:
        sql = context_identities_sql(
            XbrlContextSyncConfig(),
            ticker="TEST",
            timestamp_us=123,
            accession_number="0000000123-26-000001",
        )

        self.assertIn("FROM `market_sip_compact`.`sec_xbrl_context_v3` FINAL", sql)
        self.assertIn("ticker = 'TEST'", sql)
        self.assertIn("timestamp_us = 123", sql)
        self.assertIn("GROUP BY xbrl_row_kind, source_id", sql)

    def test_pending_source_is_durable_when_q_live_rows_are_incomplete(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.manifests: list[dict[str, object]] = []

            def execute(self, sql: str) -> str:
                if sql.lstrip().startswith("SELECT *"):
                    return json.dumps(
                        {
                            "cik": "0000000123",
                            "accession_number": "0000000123-26-000001",
                            "source_company_fact_rows": 5,
                            "source_frame_observation_rows": 3,
                            "status": "pending",
                        }
                    )
                if "FROM `q_live`.`sec_xbrl_company_fact_v3`" in sql:
                    return "\n".join(json.dumps({"company_fact_id": str(index)}) for index in range(4))
                if "FROM `q_live`.`sec_xbrl_frame_observation_v3`" in sql:
                    return json.dumps({"frame_observation_id": "1"})
                if sql.startswith("INSERT INTO `market_sip_compact`.`sec_xbrl_context_sync_manifest_v3`"):
                    self.manifests.append(json.loads(sql.split("\n", 1)[1]))
                    return ""
                raise AssertionError(sql)

        client = FakeClient()
        sync = SecXbrlContextSync(client, XbrlContextSyncConfig())

        result = sync.sync_accession(cik="0000000123", accession_number="0000000123-26-000001")

        self.assertEqual(result.status, "pending_source")
        self.assertEqual(result.missing_rows, 3)
        self.assertEqual(client.manifests[-1]["status"], "pending_source")

    def test_live_sync_inserts_only_missing_context_identities(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.identities = {("company_fact", "existing")}
                self.inserted_rows = 0

            def execute(self, sql: str) -> str:
                if "FROM `q_live`.`sec_filing_v3` AS f FINAL" in sql:
                    return json.dumps(
                        {
                            "ticker": "TEST",
                            "timestamp_us": 123,
                            "accepted_at_utc": "2026-07-10 17:13:07.000000000",
                            "cik": "0000000123",
                            "accession_number": "0000000123-26-000001",
                            "accepted_at_source": "sec_submission",
                            "mapping_confidence": 1.0,
                            "bridge_id": "bridge-1",
                        }
                    )
                if sql.lstrip().startswith("SELECT xbrl_row_kind, source_id"):
                    return "\n".join("\t".join(identity) for identity in sorted(self.identities))
                if sql.startswith("INSERT INTO `market_sip_compact`.`sec_xbrl_context_v3`"):
                    rows = [json.loads(line) for line in sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines()]
                    self.inserted_rows += len(rows)
                    self.identities.update((row["xbrl_row_kind"], row["source_id"]) for row in rows)
                    return ""
                if sql.startswith("INSERT INTO `market_sip_compact`.`sec_xbrl_context_sync_manifest_v3`"):
                    return ""
                raise AssertionError(sql)

        client = FakeClient()
        sync = SecXbrlContextSync(client, XbrlContextSyncConfig())
        rows = [
            {
                "cik": "0000000123",
                "accession_number": "0000000123-26-000001",
                "company_fact_id": source_id,
                "taxonomy": "us-gaap",
                "tag": "Assets",
                "unit_code": "USD",
                "value": 1,
            }
            for source_id in ("existing", "new")
        ]

        first = sync.sync_rows(
            cik="0000000123",
            accession_number="0000000123-26-000001",
            company_fact_rows=rows,
            frame_observation_rows=[],
        )
        second = sync.sync_rows(
            cik="0000000123",
            accession_number="0000000123-26-000001",
            company_fact_rows=rows,
            frame_observation_rows=[],
        )

        self.assertEqual(first.status, "ok")
        self.assertEqual(first.inserted_rows, 1)
        self.assertEqual(second.inserted_rows, 0)
        self.assertEqual(client.inserted_rows, 1)

    def test_gateway_marks_pending_before_source_write_then_syncs_context(self) -> None:
        calls: list[str] = []

        class FakeContext:
            def mark_pending(self, **_kwargs) -> None:
                calls.append("pending")

            def sync_rows(self, **_kwargs) -> XbrlContextSyncResult:
                calls.append("context")
                return XbrlContextSyncResult(
                    cik="0000000123",
                    accession_number="0000000123-26-000001",
                    status="ok",
                    inserted_rows=8,
                )

        class FakeManifest:
            def mark_pending(self, **_kwargs) -> None:
                calls.append("ingest_pending")

            def mark_complete(self, **_kwargs) -> None:
                calls.append("ingest_complete")

            def mark_failed(self, **_kwargs) -> None:
                calls.append("ingest_failed")

        class FakeWriter:
            def write_accession(self, **kwargs) -> SecWriteResult:
                calls.append("source")
                if kwargs["skip_existing"]:
                    raise AssertionError("live repair writes must not skip an existing partial accession")
                if kwargs["skip_same_revision"]:
                    raise AssertionError("live repair writes must replay an incomplete same-revision accession")
                return SecWriteResult(filing_rows=1, xbrl_company_fact_rows=5, xbrl_frame_observation_rows=3)

        gateway = SecGateway.__new__(SecGateway)
        gateway.config = SimpleNamespace(execute=True, xbrl_context_sync_enabled=True)
        gateway._run_id = "test-run"
        gateway._live_pipeline = SimpleNamespace(
            process_feed_item=lambda *_args, **_kwargs: SimpleNamespace(
                filing_row={"cik": "0000000123", "accession_number": "0000000123-26-000001"},
                document_rows=[],
                text_source_rows=[],
                text_rows=[],
                skip_rows=[],
                xbrl_rows=SimpleNamespace(
                    concept_rows=[],
                    company_fact_rows=[{}] * 5,
                    frame_rows=[],
                    frame_observation_rows=[{}] * 3,
                    companyfacts_status="available",
                ),
                source_cik="0000000123",
                source_version_key="revision-key",
                source_revision_at="2026-07-10 17:13:07.000",
                source_revision_rank=123,
                metadata_status="submissions_recent",
                xbrl_expected=True,
            )
        )
        gateway._writer = FakeWriter()
        gateway._live_manifest = FakeManifest()
        gateway._xbrl_context = FakeContext()
        gateway._log = lambda *_args, **_kwargs: None
        item = SecFeedItem(
            accession_number="0000000123-26-000001",
            accession_number_compact="000000012326000001",
            cik="0000000123",
            form_type="10-Q",
            title="Example",
            filing_detail_url="",
            primary_document_url="",
            updated_at_utc=datetime.now(UTC),
        )

        result = gateway._process_item(item, set())

        self.assertEqual(calls, ["ingest_pending", "pending", "source", "context", "ingest_complete"])
        self.assertEqual(result.xbrl_context_rows, 8)

    def test_packed_model_defaults_to_v3_xbrl_context(self) -> None:
        self.assertEqual(PackedContextConfig().sec_xbrl_context_table, "sec_xbrl_context_v3")
        self.assertEqual(DEFAULTS["sec_xbrl_context_table"], "sec_xbrl_context_v3")

    def test_dashboard_uses_explicit_xbrl_context_database(self) -> None:
        rows = configured_tables(
            {
                "xbrl_context_database": "market_sip_compact",
                "xbrl_context_table": "sec_xbrl_context_v3",
                "xbrl_context_manifest_table": "sec_xbrl_context_sync_manifest_v3",
            },
            read_database="q_live",
            write_database="q_live",
        )

        self.assertEqual({row["database"] for row in rows}, {"market_sip_compact"})
        self.assertEqual(metric_label("xbrl_context_reconciled_accessions"), "xbrl context reconciled")


if __name__ == "__main__":
    unittest.main()
