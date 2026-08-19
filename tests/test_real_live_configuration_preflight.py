from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from src.backend.real_live_trading_service import (
    RealLiveAccount,
    _approved_configuration_checks,
    check_ibkr,
    real_live_preflight,
    resolve_real_live_accounts,
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
        self.assertEqual(checks[0]["id"], "approved_trading_configuration")
        self.assertEqual(checks[0]["label"], "Approved configuration")
        self.assertIn("Publish", checks[0]["message"])
        self.assertEqual(checks[0]["action"]["hash"], "#revision-configuration")

    def test_paper_and_live_accounts_cannot_share_one_session(self) -> None:
        accounts = [
            self.account,
            RealLiveAccount(
                account_key="live-primary",
                account_class="margin",
                account_id="U1234567",
                label="Live primary",
                trading_mode="live",
            ),
        ]
        with patch(
            "src.backend.real_live_trading_service.configured_real_live_accounts",
            return_value=accounts,
        ):
            with self.assertRaisesRegex(ValueError, "cannot share one trading session"):
                resolve_real_live_accounts(["paper-primary", "live-primary"])

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

    def test_ibkr_readiness_preserves_order_and_runs_independent_reads_concurrently(self) -> None:
        active = 0
        peak_active = 0
        activity_lock = threading.Lock()

        def get_json(path: str, *, timeout: int) -> dict[str, object]:
            nonlocal active, peak_active
            del timeout
            with activity_lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.05)
            with activity_lock:
                active -= 1
            if path == "/iserver/auth/status":
                return {"authenticated": True}
            if path == "/iserver/accounts":
                return {"accounts": [self.account.account_id]}
            return {"netliquidation": {"amount": 100_000}}

        with patch("src.backend.real_live_trading_service.ibkr_get_json", side_effect=get_json):
            checks = check_ibkr(self.account)

        self.assertEqual(
            [check["id"] for check in checks],
            [
                "paper-primary_ibkr_auth",
                "paper-primary_ibkr_account",
                "paper-primary_ibkr_portfolio",
            ],
        )
        self.assertEqual(peak_active, 3)

    def test_preflight_preserves_check_order_while_probes_run_concurrently(self) -> None:
        active = 0
        peak_active = 0
        activity_lock = threading.Lock()

        def delayed(value: object) -> object:
            nonlocal active, peak_active
            with activity_lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.05)
            with activity_lock:
                active -= 1
            return value

        with (
            patch("src.backend.real_live_trading_service.configured_real_live_accounts", return_value=[self.account]),
            patch("src.backend.real_live_trading_service.resolve_real_live_accounts", return_value=[self.account]),
            patch("src.backend.real_live_trading_service.check_qmd_live", side_effect=lambda: delayed({"id": "qmd", "status": "ready"})),
            patch("src.backend.real_live_trading_service.check_live_strategy_runtime", side_effect=lambda mode: delayed({"id": f"runtime:{mode}", "status": "ready"})),
            patch("src.backend.real_live_trading_service.check_massive_rest", side_effect=lambda: delayed({"id": "massive", "status": "ready", "required": False})),
            patch("src.backend.real_live_trading_service._approved_configuration_checks", return_value=[{"id": "approved", "status": "ready"}]),
            patch("src.backend.real_live_trading_service.check_ibkr", side_effect=lambda account: delayed([{"id": f"ibkr:{account.account_key}", "status": "ready"}])),
            patch("src.backend.real_live_trading_service.massive_base_url", return_value="https://massive.invalid"),
            patch("src.backend.real_live_trading_service.ibkr_base_url", return_value="https://ibkr.invalid"),
        ):
            payload = real_live_preflight(account_keys=[self.account.account_key])

        self.assertGreaterEqual(peak_active, 4)
        self.assertEqual(
            [check["id"] for check in payload["checks"]],
            ["qmd", "runtime:paper", "massive", "approved", "ibkr:paper-primary"],
        )


if __name__ == "__main__":
    unittest.main()
