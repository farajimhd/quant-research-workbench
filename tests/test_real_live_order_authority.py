from __future__ import annotations

import unittest
from unittest.mock import patch

from src.backend.real_live_trading_service import (
    cancel_real_live_order,
    modify_real_live_order,
    reply_real_live_order,
    submit_real_live_order,
)


class RealLiveOrderAuthorityTests(unittest.TestCase):
    @patch("src.backend.real_live_trading_service.resolve_real_live_accounts")
    @patch("src.backend.real_live_trading_service.ibkr_post_json")
    def test_direct_submission_fails_before_account_or_broker_access(
        self, broker_post, resolve_accounts
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "Portfolio and OMS"):
            submit_real_live_order("paper", {"symbol": "AAPL", "quantity": 10})

        resolve_accounts.assert_not_called()
        broker_post.assert_not_called()

    @patch("src.backend.real_live_trading_service.ibkr_post_json")
    def test_direct_reply_is_retired(self, broker_post) -> None:
        with self.assertRaisesRegex(RuntimeError, "OMS reconciliation"):
            reply_real_live_order("reply-1", True)
        broker_post.assert_not_called()

    @patch("src.backend.real_live_trading_service.resolve_real_live_accounts")
    @patch("src.backend.real_live_trading_service.ibkr_post_json")
    def test_direct_modify_is_retired(self, broker_post, resolve_accounts) -> None:
        with self.assertRaisesRegex(RuntimeError, "owned by OMS"):
            modify_real_live_order("paper", "order-1", {"symbol": "AAPL"})
        resolve_accounts.assert_not_called()
        broker_post.assert_not_called()

    @patch("src.backend.real_live_trading_service.resolve_real_live_accounts")
    def test_direct_cancel_is_retired(self, resolve_accounts) -> None:
        with self.assertRaisesRegex(RuntimeError, "owned by OMS"):
            cancel_real_live_order("paper", "order-1")
        resolve_accounts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
