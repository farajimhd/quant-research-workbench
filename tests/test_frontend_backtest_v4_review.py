"""Opt-in browser contract for the normalized Strategy 1 saved review."""
import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("BACKTEST_REVIEW_UI"), "opt-in managed browser check")
class BacktestV4ReviewUITests(unittest.TestCase):
    def test_terminal_balances_remain_legible_at_compact_scale(self):
        from playwright.sync_api import sync_playwright

        evidence = Path(os.environ["BACKTEST_REVIEW_EVIDENCE"])
        evidence.mkdir(parents=True, exist_ok=True)
        run_id = "0d5fe74d-2e7b-4d52-94c1-6666b961eca2"
        history = {"rows": [{
            "run_id": run_id, "created_at": "2026-09-27T04:00:00Z",
            "status": "completed", "session_date": "2026-08-18",
            "tickers": [], "journal_backend": "arte_typed_journal_v4",
            "journal_sequence": 7785, "v4_review_available": True,
        }]}
        review = {
            "schema_version": "strategy-one-v4-terminal-review-page-v1",
            "status": "completed", "verified_sequence": 7785,
            "market_cursor_verified": True,
            "market_cursor": {"session_date": "2026-08-18", "boundary_ms": 19500100},
            "limitations": [],
            "financial_accounts": {"SIM-01-REPLAY": {
                "currency": "USD", "net_liquidation": 98976.46511,
                "total_cash_value": 98976.46511, "gross_position_value": 0,
                "buying_power": 98976.46511, "expected_position_count": 0,
                "source_timestamp_ms": 1787058670500,
            }},
            "events": [{"event": {
                "sequence": 1, "event_time": "2026-08-18T08:00:00Z",
                "category": "lifecycle", "entity_type": "run",
                "entity_id": "start", "account_id": "",
            }, "detail_family": "trading_run_v1",
                "detail": {"status": "running"}}],
            "next_sequence": 1, "complete": True,
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for theme, scale, width, height in (
                    ("light", 1, 1600, 1000), ("dark", 1.25, 900, 700)):
                    with self.subTest(theme=theme, scale=scale, width=width):
                        context = browser.new_context(viewport={"width": width, "height": height})
                        context.add_init_script(
                            f"localStorage.setItem('quant-research-workbench.theme','{theme}');"
                            f"localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))

                        def handle(route):
                            url = route.request.url
                            if url.endswith("/api/trading/backtest/runs"):
                                payload = history
                            elif "/v4-terminal-page" in url:
                                payload = review
                            elif "/configuration-options" in url:
                                payload = {"candidates": [], "candidate_id": "",
                                           "run_plan_id": "", "available_run_plans": [],
                                           "error": ""}
                            elif url.endswith("/backtest/structure-books"):
                                payload = {"items": []}
                            else:
                                payload = {}
                            route.fulfill(json=payload)

                        page.route("**/api/trading/**", handle)
                        page.goto("http://127.0.0.1:5173/#backtest-trading")
                        page.get_by_role("button", name=f"Review backtest {run_id[:8]}").click()
                        page.get_by_text("7,785 verified journal records").wait_for()
                        cash = page.locator(
                            '[aria-label="Terminal account balances"] tbody td:nth-child(3)')
                        self.assertEqual(cash.inner_text(), "$98,976.47")
                        self.assertEqual(cash.evaluate(
                            "node => getComputedStyle(node).whiteSpace"), "nowrap")
                        self.assertEqual(errors, [])
                        section = page.locator(".backtest-v4-review")
                        section.scroll_into_view_if_needed()
                        section.screenshot(path=str(evidence / f"v4-{theme}-{scale}-{width}.png"))
                        context.close()
            finally:
                browser.close()
