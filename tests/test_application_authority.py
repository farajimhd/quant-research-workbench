from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.backend.app import app, bounded_computation_planner_summary
from src.backend.application_authority import (
    AuthorityDenied,
    AuthorityPolicy,
    classify_command,
    infer_mode,
)


class ApplicationAuthorityTests(unittest.TestCase):
    def test_watchlist_planner_summary_omits_wide_requirement_and_target_rows(self) -> None:
        requirements, demand = bounded_computation_planner_summary({
            "schema_version": 1,
            "complete": True,
            "active_requirement_count": 2,
            "live_requirement_count": 2,
            "offline_requirement_count": 0,
            "requirements": [{"requirement_id": "wide"}],
            "live_demand": {
                "active_symbol_count": 5,
                "active_target_count": 1,
                "active_requirement_count": 2,
                "requirements": [{"requirement_id": "wide"}],
                "requirement_ref_counts": {"wide": 2},
                "targets": [{"target_id": "wide"}],
            },
        })

        self.assertEqual(requirements["active_requirement_count"], 2)
        self.assertNotIn("requirements", requirements)
        self.assertEqual(demand["active_symbol_count"], 5)
        self.assertNotIn("requirements", demand)
        self.assertNotIn("requirement_ref_counts", demand)
        self.assertNotIn("targets", demand)

    def test_local_policy_uses_system_identity_and_infers_command_scope(self) -> None:
        policy = AuthorityPolicy.from_environment({})
        authority = policy.authorize(
            method="POST",
            path="/api/trading/replay/runs/run-1/trade-proposals",
            headers={"origin": "http://localhost:5173", "x-qw-mode": "replay"},
            client_host="127.0.0.1",
        )
        self.assertEqual(authority.user_id, "local-user")
        self.assertEqual(authority.workspace_id, "local")
        self.assertEqual(authority.mode, "replay")
        self.assertEqual(authority.command, "trading.proposal")

    def test_live_and_paper_proposals_infer_their_runtime_mode(self) -> None:
        self.assertEqual(infer_mode("/api/trading/live/trade-proposals"), "live")
        self.assertEqual(infer_mode("/api/trading/paper/trade-proposals"), "paper")

    def test_local_policy_rejects_remote_clients_and_bad_browser_origins(self) -> None:
        policy = AuthorityPolicy.from_environment({})
        with self.assertRaisesRegex(AuthorityDenied, "loopback"):
            policy.authorize(method="GET", path="/api/health", headers={}, client_host="10.0.0.5")
        with self.assertRaisesRegex(AuthorityDenied, "not allowed"):
            policy.authorize(
                method="POST",
                path="/api/trading/configuration/publish",
                headers={"origin": "https://attacker.example"},
                client_host="127.0.0.1",
            )

    def test_policy_enforces_environment_mode_account_and_command_allowlists(self) -> None:
        policy = AuthorityPolicy.from_environment(
            {
                "BACKEND_AUTHORITY_ALLOWED_MODES": "replay",
                "BACKEND_AUTHORITY_ALLOWED_ACCOUNTS": "paper-main",
                "BACKEND_AUTHORITY_ALLOWED_COMMANDS": "read,trading.command",
            }
        )
        with self.assertRaisesRegex(AuthorityDenied, "Mode"):
            policy.authorize(method="GET", path="/api/trading/backtest/runs", headers={}, client_host="testclient")
        with self.assertRaisesRegex(AuthorityDenied, "Account"):
            policy.authorize(
                method="POST",
                path="/api/trading/portfolio-management/cash-main/commands",
                headers={"x-qw-mode": "replay"},
                client_host="testclient",
            )
        with self.assertRaisesRegex(AuthorityDenied, "Command"):
            policy.authorize(
                method="POST",
                path="/api/trading/configuration/publish",
                headers={"x-qw-mode": "replay"},
                client_host="testclient",
            )

    def test_proxy_mode_requires_shared_token_and_identity_headers(self) -> None:
        policy = AuthorityPolicy.from_environment(
            {
                "BACKEND_AUTHORITY_MODE": "proxy",
                "BACKEND_AUTHORITY_PROXY_TOKEN": "secret-token",
            }
        )
        with self.assertRaisesRegex(AuthorityDenied, "token"):
            policy.authorize(method="GET", path="/api/health", headers={}, client_host="10.0.0.5")
        authority = policy.authorize(
            method="GET",
            path="/api/health",
            headers={
                "x-qw-authority-token": "secret-token",
                "x-qw-user": "operator-7",
                "x-qw-workspace": "desk-west",
            },
            client_host="10.0.0.5",
        )
        self.assertEqual(authority.user_id, "operator-7")
        self.assertEqual(authority.workspace_id, "desk-west")

    def test_route_classification_is_deterministic(self) -> None:
        self.assertEqual(classify_command("GET", "/api/health"), "read")
        self.assertEqual(classify_command("POST", "/api/market-data/build/jobs"), "market_data.build_control")
        self.assertEqual(infer_mode("/api/trading/backtest_debug/runs"), "backtest_debug")

    def test_policy_review_never_exposes_proxy_token(self) -> None:
        policy = AuthorityPolicy.from_environment(
            {
                "BACKEND_AUTHORITY_MODE": "proxy",
                "BACKEND_AUTHORITY_PROXY_TOKEN": "secret-token",
            }
        )
        payload = policy.public_payload()
        self.assertNotIn("secret-token", str(payload))
        self.assertTrue(payload["proxy_token_configured"])

    def test_application_middleware_rejects_cross_origin_before_route_dispatch(self) -> None:
        with TestClient(app) as client:
            rejected = client.post(
                "/api/authority-test-missing",
                headers={"origin": "https://attacker.example"},
            )
            allowed_origin = client.post(
                "/api/authority-test-missing",
                headers={"origin": "http://localhost:5173"},
            )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["error"]["code"], "browser_origin_denied")
        self.assertEqual(allowed_origin.status_code, 405)

    def test_system_authority_endpoint_exposes_effective_policy_without_secrets(self) -> None:
        with TestClient(app) as client:
            response = client.get("/api/system/authority")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["policy"]["schema_version"], "authority-policy.v1")
        self.assertEqual(payload["request_authority"]["command"], "read")
        self.assertNotIn("proxy_token", payload["policy"])

    def test_websocket_route_rejects_unapproved_origin_before_upstream_connect(self) -> None:
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as raised:
                with client.websocket_connect(
                    "/api/trading/news/stream",
                    headers={"origin": "https://attacker.example"},
                ):
                    pass
        self.assertEqual(raised.exception.code, 4403)


if __name__ == "__main__":
    unittest.main()
