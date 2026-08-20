from __future__ import annotations

import unittest

from bar_gpt_service.config import _release_configs, _selected_releases


class ReleaseIdentityTests(unittest.TestCase):
    def test_manifest_requires_immutable_hashes(self) -> None:
        with self.assertRaisesRegex(ValueError, "require 64-character"):
            _release_configs(
                {
                    "BAR_GPT_RELEASES_JSON": """
                    [{
                      "model_id": "unverified",
                      "version": "v2",
                      "checkpoint": "D:/runtime/checkpoint.pt",
                      "role": "shadow"
                    }]
                    """
                }
            )

    def test_manifest_identity_survives_operational_selection(self) -> None:
        releases = _release_configs(
            {
                "BAR_GPT_RELEASES_JSON": """
                [{
                  "model_id": "bar_gpt_v3_fixed_3500m",
                  "version": "v3",
                  "checkpoint": "D:/runtime/checkpoint.pt",
                  "checkpoint_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "contract_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "role": "shadow"
                }]
                """
            }
        )
        selected = _selected_releases(
            releases,
            {
                "selected_release_ids": ["bar_gpt_v3_fixed_3500m"],
                "release_roles": {"bar_gpt_v3_fixed_3500m": "champion"},
            },
        )

        self.assertEqual(selected[0].expected_checkpoint_hash, "a" * 64)
        self.assertEqual(selected[0].expected_contract_hash, "b" * 64)
        self.assertEqual(selected[0].role, "champion")


if __name__ == "__main__":
    unittest.main()
