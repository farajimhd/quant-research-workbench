from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.reference_gateway.presentation_assets import (
    POLICY_VERSION,
    classify_sec_candidate,
    image_dimensions_and_mime,
    resolve_presentations,
    sec_identity_match,
    selection_reason,
)


def png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"


def sec_document(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "document_id": "doc-1",
        "accession_number": "0001104659-25-110025",
        "cik": "0001513845",
        "sequence_number": 2,
        "document_name": "lg_nebiusnew-4clr.jpg",
        "document_url": "https://www.sec.gov/Archives/edgar/data/1513845/logo.png",
        "mime_type": "image/png",
        "content_sha256": "",
        "source_revision_at": "2025-11-12 12:00:00.000",
        "source_revision_rank": 1,
        "source_archive_date": "2025-11-12",
        "accepted_at_utc": "2025-11-12 12:00:00.000",
        "form_type": "424B5",
        "issuer_id": "issuer:cik:0001513845",
        "listing_id": "listing:nbis",
        "ticker": "NBIS",
        "issuer_name": "Nebius Group N.V.",
        "branding_name": "Nebius",
    }
    row.update(overrides)
    return row


class PresentationAssetsTest(unittest.TestCase):
    def test_png_dimensions_are_deterministic(self) -> None:
        width, height, mime = image_dimensions_and_mime(png_bytes(96, 64), "")
        self.assertEqual((width, height, mime), (96, 64, "image/png"))

    def test_sec_identity_requires_ticker_or_issuer_token_in_filename(self) -> None:
        matched, evidence = sec_identity_match(sec_document())
        self.assertTrue(matched)
        self.assertEqual(evidence["issuer_token_matches"], ["nebius"])

        matched, evidence = sec_identity_match(sec_document(document_name="asyousowlogo.jpg"))
        self.assertFalse(matched)
        self.assertFalse(evidence["ticker_match"])

    def test_known_third_party_filename_is_rejected_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "services.reference_gateway.presentation_assets.fetch_sec_asset"
        ) as fetch:
            asset, candidate = classify_sec_candidate(
                sec_document(document_name="asyousowlogo.jpg"),
                asset_root=Path(temp_dir),
                user_agent="test@example.com",
                timeout_seconds=1,
                max_bytes=1_000_000,
                run_id="test",
            )
        self.assertIsNone(asset)
        self.assertEqual(candidate["candidate_status"], "rejected")
        self.assertEqual(candidate["status_reason"], "third_party_filename_token")
        fetch.assert_not_called()

    def test_verified_compact_sec_logo_outranks_massive_icon(self) -> None:
        content = png_bytes(96, 64)
        document = sec_document(content_sha256=hashlib.sha256(content).hexdigest())
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "services.reference_gateway.presentation_assets.fetch_sec_asset", return_value=content
        ):
            asset, candidate = classify_sec_candidate(
                document,
                asset_root=Path(temp_dir),
                user_agent="test@example.com",
                timeout_seconds=1,
                max_bytes=1_000_000,
                run_id="test",
            )
            self.assertIsNotNone(asset)
            self.assertTrue((Path(temp_dir) / str(asset["relative_path"])).exists())
        self.assertEqual(candidate["candidate_status"], "accepted")
        self.assertEqual(candidate["quality_class"], "compact_mark")
        self.assertEqual(candidate["quality_score"], 1010.0)
        self.assertGreater(candidate["quality_score"], 800.0)

    def test_policy_reason_order_is_explicit(self) -> None:
        self.assertEqual(POLICY_VERSION, "presentation_asset_policy_v1")
        self.assertEqual(selection_reason("sec_filing_logo", "compact_mark"), "verified_sec_compact_mark")
        self.assertEqual(selection_reason("massive_icon", "compact_mark"), "massive_compact_icon_fallback")
        self.assertEqual(selection_reason("sec_filing_logo", "wordmark"), "verified_sec_wordmark_fallback")
        self.assertEqual(selection_reason("massive_logo", "wordmark"), "massive_logo_fallback")

    def test_resolver_replaces_old_selection_only_with_higher_ranked_accepted_asset(self) -> None:
        class FakeClient:
            def execute(self, sql: str) -> str:
                if "FROM `q_live`.market_issuer_presentation_candidate_v1" in sql:
                    rows = [
                        {
                            "issuer_id": "issuer:1",
                            "asset_id": "sec-compact",
                            "source_system": "sec_edgar",
                            "source_kind": "sec_filing_logo",
                            "quality_class": "compact_mark",
                            "quality_score": 1000.0,
                            "observed_at_utc": "2026-08-20 00:00:00.000",
                            "candidate_id": "candidate:sec",
                        },
                        {
                            "issuer_id": "issuer:1",
                            "asset_id": "massive-icon",
                            "source_system": "massive",
                            "source_kind": "massive_icon",
                            "quality_class": "compact_mark",
                            "quality_score": 800.0,
                            "observed_at_utc": "2026-08-19 00:00:00.000",
                            "candidate_id": "candidate:massive",
                        },
                    ]
                else:
                    rows = [{"issuer_id": "issuer:1", "asset_id": "massive-icon", "policy_version": POLICY_VERSION}]
                return "\n".join(json.dumps(row) for row in rows)

        selections = resolve_presentations(FakeClient(), database="q_live", issuer_ids=["issuer:1"], run_id="test")
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0]["asset_id"], "sec-compact")
        self.assertEqual(selections[0]["selection_reason"], "verified_sec_compact_mark")


if __name__ == "__main__":
    unittest.main()
