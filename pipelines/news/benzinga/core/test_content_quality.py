from __future__ import annotations

import unittest

from .content_quality import (
    sanitize_packed_news_text,
    transport_artifact_reasons,
)


class NewsContentQualityTest(unittest.TestCase):
    def test_bot_challenge_is_transport_artifact(self) -> None:
        reasons = transport_artifact_reasons(
            "As you were browsing, something made us think you were a bot. "
            "Please stand by. We're getting everything ready."
        )
        self.assertIn("bot_challenge", reasons)
        self.assertIn("loading_gate", reasons)

    def test_javascript_gate_is_transport_artifact(self) -> None:
        self.assertEqual(
            transport_artifact_reasons(
                "To use this website, please enable JavaScript."
            ),
            ("javascript_gate",),
        )

    def test_packed_sanitizer_removes_only_rejected_external_block(self) -> None:
        packed = "\n".join((
            "Title: Issuer reports results",
            "Source [provider_body:0] https://provider.test/story",
            "Issuer revenue increased 20%.",
            "Source [external:1]",
            "To use this website, please enable JavaScript.",
            "Source [pdf:1] https://provider.test/report.pdf",
            "Report appendix.",
        ))
        sanitized, reasons = sanitize_packed_news_text(packed)
        self.assertIn("Issuer revenue increased 20%", sanitized)
        self.assertIn("Report appendix", sanitized)
        self.assertNotIn("enable JavaScript", sanitized)
        self.assertEqual(reasons, ("javascript_gate",))

    def test_provider_body_is_never_removed_by_compatibility_guard(self) -> None:
        packed = "\n".join((
            "Title: Issuer discusses web access",
            "Source [provider_body:0]",
            "The issuer told customers to enable JavaScript.",
        ))
        sanitized, reasons = sanitize_packed_news_text(packed)
        self.assertEqual(sanitized, packed)
        self.assertEqual(reasons, ())


if __name__ == "__main__":
    unittest.main()
