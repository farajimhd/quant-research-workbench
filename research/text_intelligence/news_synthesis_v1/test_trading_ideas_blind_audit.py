from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .trading_ideas_blind_audit import compact_preview, validate_oversized_chunk_notes


class TradingIdeasBlindAuditTests(unittest.TestCase):
    def test_compact_preview_keeps_title_teaser_and_three_opening_sentences(self) -> None:
        text = "\n".join((
            "Title: Example title",
            "Teaser: Example teaser.",
            "Source [provider_body:0] https://example.test",
            "First sentence. Second sentence! Third sentence? Fourth sentence.",
        ))
        preview = compact_preview(text)
        self.assertEqual(preview["title"], "Example title")
        self.assertEqual(preview["teaser"], "Example teaser.")
        self.assertEqual(len(preview["opening_sentences"]), 3)
        self.assertNotIn("Fourth sentence", preview["preview_text"])

    def test_oversized_chunk_notes_require_exact_in_chunk_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = {
                "review_id": "TI1",
                "chunk_id": "OS0000",
                "rendered_text_chunk": "A new issuer event occurred.",
            }
            (root / "OS0000.json").write_text(json.dumps(chunk), encoding="utf-8")
            notes = root / "notes.jsonl"
            notes.write_text(json.dumps({
                "review_id": "TI1",
                "chunk_id": "OS0000",
                "contains_potential_new_issuer_event": True,
                "evidence_excerpt": "A new issuer event occurred.",
                "notes": "Reports a current issuer event.",
                "attestation": {"used_only_supplied_packet": True, "used_external_context": False},
            }) + "\n", encoding="utf-8")
            result = validate_oversized_chunk_notes(chunk_root=root, notes_path=notes)
            self.assertEqual(result["chunks"], 1)


if __name__ == "__main__":
    unittest.main()
