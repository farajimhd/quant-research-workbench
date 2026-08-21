from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .provider_context_rule_audit import prepare_adjudication, proposed_rule_names


class ProviderContextRuleAuditTests(unittest.TestCase):
    def test_safe_analyst_channel_family(self) -> None:
        names = proposed_rule_names({
            "provider": "benzinga",
            "channels": ["analyst ratings", "news", "price target", "reiteration"],
            "provider_tags": [],
        })
        self.assertIn("benzinga_direct_analyst_action_family", names)

    def test_mixed_earnings_analyst_family_is_excluded(self) -> None:
        names = proposed_rule_names({
            "provider": "benzinga",
            "channels": ["analyst ratings", "earnings", "news", "price target"],
            "provider_tags": [],
        })
        self.assertNotIn("benzinga_direct_analyst_action_family", names)

    def test_rules_require_benzinga_authority(self) -> None:
        names = proposed_rule_names({
            "provider": "other",
            "channels": ["movers"],
            "provider_tags": ["rsi"],
        })
        self.assertEqual(names, ())

    def test_adjudication_packet_hides_votes_and_contains_only_disagreements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = [
                {"review_id": "r1", "rendered_text": "exact one"},
                {"review_id": "r2", "rendered_text": "exact two"},
            ]
            reviews = (
                [
                    {"review_id": "r1", "manual_label": "eligible", "confidence_probability": 0.8, "evidence_excerpt": "exact one", "isolation_attestation": {"used_only_supplied_packet": True, "used_external_context": False}},
                    {"review_id": "r2", "manual_label": "ineligible", "confidence_probability": 0.8, "evidence_excerpt": "exact two", "isolation_attestation": {"used_only_supplied_packet": True, "used_external_context": False}},
                ],
                [
                    {"review_id": "r1", "manual_label": "ineligible", "confidence_probability": 0.8, "evidence_excerpt": "exact one", "isolation_attestation": {"used_only_supplied_packet": True, "used_external_context": False}},
                    {"review_id": "r2", "manual_label": "ineligible", "confidence_probability": 0.8, "evidence_excerpt": "exact two", "isolation_attestation": {"used_only_supplied_packet": True, "used_external_context": False}},
                ],
            )
            for name, rows in (("BLIND_REVIEW_PACKET.jsonl", packet), ("one.jsonl", reviews[0]), ("two.jsonl", reviews[1])):
                (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            report = prepare_adjudication(root, root / "one.jsonl", root / "two.jsonl")
            self.assertEqual(report["disagreements"], 1)
            output = json.loads((root / "BLIND_ADJUDICATION_PACKET.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(output, packet[0])
            self.assertNotIn("manual_label", output)
