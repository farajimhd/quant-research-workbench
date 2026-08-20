from __future__ import annotations

import json
import unittest

from services.reference_gateway.market_publications import market_publication_audit


class MarketPublicationAuditTest(unittest.TestCase):
    def test_append_only_selection_history_is_audited_without_final(self) -> None:
        queries: list[str] = []

        class FakeClient:
            def execute(self, sql: str) -> str:
                if "FROM system.tables" in sql:
                    return "1"
                queries.append(sql)
                return json.dumps({"rows": 0, "min_value": None, "max_value": None})

        rows = market_publication_audit(FakeClient(), database="q_live")  # type: ignore[arg-type]

        self.assertTrue(rows)
        selection_query = next(
            query for query in queries if "market_issuer_presentation_selection_v1" in query
        )
        candidate_query = next(
            query for query in queries if "market_issuer_presentation_candidate_v1" in query
        )
        self.assertNotIn(" FINAL", selection_query)
        self.assertIn(" FINAL", candidate_query)


if __name__ == "__main__":
    unittest.main()
