from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.real_live_trading_service import (
    RealLiveAccount,
    _approved_configuration_checks,
)


class RealLiveConfigurationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = RealLiveAccount(
            account_key="paper-primary",
            account_class="margin",
            account_id="DU1234567",
            label="Paper primary",
            trading_mode="paper",
        )

    def test_missing_approved_release_blocks_broker_operation(self) -> None:
        with patch(
            "src.backend.trading_configuration_service.approved_configuration",
            return_value=None,
        ):
            checks = _approved_configuration_checks([self.account])

        self.assertEqual(checks[0]["status"], "blocked")

    def test_exact_account_and_enabled_mode_deployment_are_required(self) -> None:
        release = {
            "revision": 7,
            "payload": {
                "accounts": {"bindings": [{
                    "account_key": "paper-primary",
                    "source_account_id": "DU1234567",
                    "enabled": True,
                    "modes": ["paper"],
                }]},
                "assignments": {"deployments": [{
                    "deployment_id": "paper-balanced",
                    "enabled": True,
                    "modes": ["paper"],
                }]},
                "portfolio": {"mandates": [{
                    "account_key": "paper-primary",
                    "deployment_id": "paper-balanced",
                    "enabled": True,
                }]},
            },
        }
        with patch(
            "src.backend.trading_configuration_service.approved_configuration",
            return_value=release,
        ):
            ready = _approved_configuration_checks([self.account])
            release["payload"]["accounts"]["bindings"][0]["source_account_id"] = "DU7654321"
            mismatched = _approved_configuration_checks([self.account])
            release["payload"]["accounts"]["bindings"][0]["source_account_id"] = "DU1234567"
            release["payload"]["portfolio"]["mandates"] = []
            unmandated = _approved_configuration_checks([self.account])

        self.assertEqual(ready[0]["status"], "ready")
        self.assertEqual(mismatched[0]["status"], "blocked")
        self.assertEqual(unmandated[0]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
