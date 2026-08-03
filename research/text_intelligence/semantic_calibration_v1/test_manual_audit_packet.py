from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .comparison import CollectionItem
from .fresh_acceptance_audit import load_gateway_source_evidence
from .manual_audit_packet import (
    render_bounded_manual_review_packet,
    audit_path,
    render_compact_manual_review_packet,
    render_manual_review_packet,
)


class _StaticClickHouseClient:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def execute(self, _sql: str) -> str:
        return "\n".join(json.dumps(row) for row in self.rows)


class ManualAuditPacketTests(unittest.TestCase):
    @staticmethod
    def _item() -> CollectionItem:
        return CollectionItem(
            sample_id="N0001",
            split="validation",
            blinded={
                "source_id": "source-1",
                "source_timestamp": "2026-07-01T12:00:00Z",
            },
            truth={},
        )

    def test_packet_preserves_required_sections_and_decodes_html(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "N0001_case.audit.md"
            sections = "\n\n".join(
                f"## {name}\n\n<pre>A&amp;B</pre>"
                for name in (
                    "Original provider payload downloaded by News Gateway",
                    "Complete News Gateway retained record",
                    "Original news texts",
                    "Audit summary",
                    "Article-level labels",
                    "Issuer-level labels",
                    "Human evidence and rationale",
                    "V9 deterministic rule trace",
                )
            )
            path.write_text(f"# N0001 - Case\n\n{sections}\n", encoding="utf-8")
            self.assertEqual(audit_path(root, "N0001"), path)
            packet = render_manual_review_packet(path)
            self.assertIn("A&B", packet)
            self.assertNotIn("Rendered article used for review", packet)

    def test_compact_packet_keeps_exact_source_without_duplicate_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "N0001_case.audit.md"
            parts = []
            names = (
                "Original provider payload downloaded by News Gateway",
                "Complete News Gateway retained record",
                "Original news texts",
                "Audit summary",
                "Article-level labels",
                "Issuer-level labels",
                "Human evidence and rationale",
                "V9 deterministic rule trace",
            )
            for name in names:
                if name == "Original provider payload downloaded by News Gateway":
                    body = '<pre>{"title":"Exact","body":"duplicate","tickers":["ABC"]}</pre>'
                elif name == "Original news texts":
                    body = "<pre>Exact source</pre>"
                else:
                    body = "evidence"
                parts.append(f"## {name}\n\n{body}")
            path.write_text("# N0001 - Exact\n\n" + "\n\n".join(parts), encoding="utf-8")
            packet = render_compact_manual_review_packet(path)
            self.assertIn('"tickers": [', packet)
            self.assertIn("Exact source", packet)
            self.assertNotIn("duplicate", packet)

    def test_gateway_source_is_strict_by_default(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "missing"):
            load_gateway_source_evidence(
                _StaticClickHouseClient([]),
                [self._item()],
            )

    def test_explicit_unavailable_mode_preserves_auditable_identity(self) -> None:
        evidence = load_gateway_source_evidence(
            _StaticClickHouseClient([]),
            [self._item()],
            allow_missing=True,
        )["source-1"]
        self.assertFalse(evidence.source_authority_available)
        self.assertEqual(
            evidence.retained_record["audit_availability"],
            "missing_gateway_retained_record",
        )
        self.assertIn("No q_live", evidence.unavailable_reason)


if __name__ == "__main__":
    unittest.main()
