from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipelines.market_sip.events import clickhouse_build_text_tokens as tokens
from pipelines.reference_data import sec_issuer_relationships as relationships
from pipelines.reference_data.migration import step_06_build_q_live_bridge_features as bridge
from pipelines.sec.edgar import sec_historical_gap_fill as historical
from services.reference_gateway import publication_rebuild


class FakeIdentifierClient:
    def execute(self, _sql: str) -> str:
        rows = []
        for index, cik in enumerate(
            ["0000083246", "0001089113", "0000312070", "0000312069", "0001114446", "0001610520", "0001383951", "0001163653"]
        ):
            rows.append(json.dumps({"cik": cik, "issuer_id": f"issuer-{index}"}))
        return "\n".join(rows)


class SecIssuerRelationshipTests(unittest.TestCase):
    def test_curated_relationships_are_official_evidence_backed_and_resolvable(self) -> None:
        definitions = relationships.load_relationship_definitions()
        resolved = relationships.resolve_relationships(
            FakeIdentifierClient(),
            database="q_live",
            definitions=definitions,
        )

        self.assertEqual(len(definitions), 4)
        self.assertEqual(len(resolved), 4)
        self.assertTrue(all(row["evidence_url"].startswith("https://www.sec.gov/") for row in definitions))
        self.assertTrue(all(row.relationship_type == "listed_ultimate_parent" for row in resolved))
        self.assertEqual(len({row.relationship_id for row in resolved}), 4)

    def test_relationship_table_uses_stable_identity_and_validity_dates(self) -> None:
        ddl = relationships.relationship_table_ddl("q_live", "id_issuer_relationship_v1", "live_market_ssd")

        self.assertIn("child_issuer_id String", ddl)
        self.assertIn("parent_issuer_id String", ddl)
        self.assertIn("valid_to_date_exclusive Nullable(Date)", ddl)
        self.assertIn("ReplacingMergeTree(last_seen_at_utc)", ddl)

    def test_bridge_adds_parent_mapping_only_without_direct_listing(self) -> None:
        sql = bridge.sec_market_bridge_source_ctes_sql("`q_live`")

        self.assertIn("id_issuer_relationship_v1", sql)
        self.assertIn("filing_issuer_to_listed_parent", sql)
        self.assertIn("direct.cik = ''", sql)
        self.assertNotIn("direct.cik IS NULL", sql)
        self.assertIn("rel.parent_issuer_id", sql)
        self.assertIn("rel.child_cik AS cik", sql)
        self.assertIn("ex.iso_country_code = 'US'", sql)
        self.assertIn("sym.instrument_type IN ('ADRC', 'CS')", sql)

    def test_targeted_embedding_selector_accepts_normalized_cik_allowlist(self) -> None:
        self.assertEqual(tokens.parse_sec_ciks("83246,0000312070,83246"), ("0000083246", "0000312070"))
        sql = tokens.sec_rendered_source_ctes_sql(
            source_database="q_live",
            filing_table="sec_filing_v3",
            document_table="sec_filing_document_v3",
            rendered_text_table="sec_filing_text_rendered_v3",
            bridge_table="id_sec_market_bridge_v3",
            start_sql="toDateTime64('2019-01-01', 3, 'UTC')",
            end_sql="toDateTime64('2027-01-01', 3, 'UTC')",
            ciks=("0000083246",),
        )
        self.assertIn("f.cik IN ('0000083246')", sql)

    def test_historical_finalize_syncs_relationships_before_bridge(self) -> None:
        with mock.patch("sys.argv", ["sec_historical_gap_fill.py", "--finalize-only", "--execute"]):
            args = historical.parse_args()
        stages = [command.stage for command in historical.build_commands(args, Path("C:/tmp/sec-rel-tests"))]

        self.assertLess(stages.index("sec-issuer-relationship-sync"), stages.index("sec-bridge-rebuild"))

    def test_reference_gateway_syncs_relationships_before_bridge(self) -> None:
        config = SimpleNamespace(
            execute=True,
            test_write_mode=False,
            rebuild_tradable_in_test_mode=False,
            prepared_root_win=Path(tempfile.gettempdir()),
            clickhouse_write_database="q_live",
        )
        completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with mock.patch.object(publication_rebuild.subprocess, "run", side_effect=[completed, completed]) as run:
            result = publication_rebuild.rebuild_sec_market_bridge(config, reason="test")

        self.assertEqual(result.status, "completed")
        self.assertIn("sync_sec_issuer_relationships.py", str(run.call_args_list[0].args[0][1]))
        self.assertIn("step_06_build_q_live_bridge_features.py", str(run.call_args_list[1].args[0][1]))


if __name__ == "__main__":
    unittest.main()
