from __future__ import annotations

import datetime as dt
import json
import unittest

from research.news_reaction_model.certified_targets_v1 import HORIZONS
from research.news_reaction_model.certified_targets_v1.build import (
    audit_authority_coverage,
    chunk_insert_sql,
    create_sql,
    parse_args,
)
from research.news_reaction_model.run_certified_v16_v17_build import commands, parse_args as parse_pipeline_args


class FakeClient:
    def __init__(self, response: dict[str, int]) -> None:
        self.response = response
        self.query = ""

    def execute(self, query: str) -> str:
        self.query = query
        return json.dumps(self.response)


class CertifiedTargetsTests(unittest.TestCase):
    def test_schema_and_insert_keep_targets_embedding_independent(self) -> None:
        args = parse_args([])
        schema = "\n".join(create_sql(args))
        insert = chunk_insert_sql(
            args,
            dt.date(2026, 7, 1),
            dt.date(2026, 8, 1),
            "signature",
        )
        self.assertIn("anchor_prices Array(Float64)", schema)
        self.assertIn("corporate_action_overlap Array(UInt8)", schema)
        self.assertNotIn("embedding", schema.lower())
        self.assertNotIn("openai_embedding", insert)
        self.assertIn("corporate_action_overlap = 0", insert)
        self.assertIn("low_return <= target_return", insert)
        self.assertIn("target_return <= high_return", insert)

    def test_authority_gate_requires_all_ten_unique_horizons(self) -> None:
        rows = 3
        response = {
            "source_rows": rows,
            "label_rows": rows * len(HORIZONS) + 20,
            "matched_label_rows": rows * len(HORIZONS),
            "unique_matched_label_rows": rows * len(HORIZONS),
            "incomplete_source_rows": 0,
        }
        client = FakeClient(response)
        result = audit_authority_coverage(
            client,
            parse_args([]),
            dt.date(2026, 7, 1),
            dt.date(2026, 8, 1),
        )
        self.assertEqual(result, response)
        self.assertIn("uniqExact(horizon_code)", client.query)

    def test_authority_gate_rejects_missing_horizon(self) -> None:
        client = FakeClient(
            {
                "source_rows": 1,
                "label_rows": len(HORIZONS) - 1,
                "matched_label_rows": len(HORIZONS) - 1,
                "unique_matched_label_rows": len(HORIZONS) - 1,
                "incomplete_source_rows": 1,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "not complete"):
            audit_authority_coverage(
                client,
                parse_args([]),
                dt.date(2026, 7, 1),
                dt.date(2026, 8, 1),
            )

    def test_gated_launcher_never_profiles_or_trains(self) -> None:
        args = parse_pipeline_args([])
        pipeline = commands(args)
        self.assertEqual(tuple(pipeline), ("authority", "sidecar", "benchmark", "v16", "v17"))
        flattened = " ".join(" ".join(command) for command in pipeline.values())
        self.assertNotIn("run_profile", flattened)
        self.assertNotIn("run_train", flattened)


if __name__ == "__main__":
    unittest.main()
