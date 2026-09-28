"""Opt-in browser contract for the normalized Strategy 1 saved review."""
import os
import unittest
from pathlib import Path


def _performance_page():
    return {"schema_version": "strategy-one-v4-performance-report-v1",
            "report": {"schema_version": 2,
                       "episode_definition": "flat_to_flat_position_lifecycle",
                       "summary": {}, "episodes": [], "equity_curve": [],
                       "pnl_candles": {"30m": [], "1h": [], "1d": [], "1M": []},
                       "strategies": [], "execution": {}, "risk": {}, "scope": {}},
            "position_lifecycles": [], "fill_count": 0, "fee_count": 0}


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
            "run": {"run_id": run_id, "session_date": "2026-08-18",
                    "strategy_id": "early-squeeze-strategy", "strategy_revision": 1},
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
                            if url.split("?", 1)[0].endswith("/api/trading/backtest/runs"):
                                payload = history
                            elif "/v4-terminal-page" in url:
                                payload = review
                            elif "/v4-performance" in url:
                                payload = _performance_page()
                            elif "/v4-trade-history" in url:
                                payload = {"fills": [], "commissions": [],
                                           "next_fill_sequence": 0,
                                           "next_commission_sequence": 0,
                                           "complete": True}
                            elif "/v4-order-history" in url:
                                payload = {"commands": [], "transitions": [],
                                           "next_command_sequence": 0,
                                           "next_transition_sequence": 0,
                                           "complete": True}
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
                        canvas = page.locator(".backtest-v4-canvas-review")
                        canvas.get_by_text("7,785 verified records").wait_for()
                        canvas.get_by_role("button", name="Canvas management").click()
                        library = canvas.get_by_role("region", name="Container library")
                        library.locator("article").filter(has_text="Portfolio").first.get_by_role(
                            "button", name="Add").click()
                        cash = canvas.get_by_text("$98,976.47", exact=True).first
                        self.assertEqual(cash.inner_text(), "$98,976.47")
                        self.assertEqual(cash.evaluate(
                            "node => getComputedStyle(node).whiteSpace"), "nowrap")
                        self.assertEqual(errors, [])
                        section = canvas
                        section.scroll_into_view_if_needed()
                        section.screenshot(path=str(evidence / f"v4-{theme}-{scale}-{width}.png"))
                        context.close()
            finally:
                browser.close()

    def test_resident_v4_run_uses_progress_then_typed_review(self):
        from playwright.sync_api import sync_playwright

        evidence = Path(os.environ["BACKTEST_REVIEW_EVIDENCE"])
        evidence.mkdir(parents=True, exist_ok=True)
        run_id = "511354df-2cf5-4a1d-91b7-6461dc1a2176"
        for theme, scale, width, height in (
            ("light", 1, 1440, 900), ("dark", 1.25, 900, 700),
        ):
            with self.subTest(theme=theme, scale=scale, width=width):
                state = {"completed": False, "legacy_requests": []}
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        context = browser.new_context(viewport={"width": width, "height": height})
                        context.add_init_script(
                            f"localStorage.setItem('quant-research-workbench.theme','{theme}');"
                            f"localStorage.setItem('quant-research-workbench.ui-scale','{scale}');"
                            f"sessionStorage.setItem('backtest.active-run.v1','{run_id}');")
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))

                        def handle(route):
                            url = route.request.url
                            if "/canvas" in url or "/results" in url or "/comparison" in url:
                                state["legacy_requests"].append(url)
                            if url.split("?", 1)[0].endswith("/api/trading/backtest/runs"):
                                payload = {"rows": [{"run_id": run_id,
                                    "strategy_name": "Strategy 1", "strategy_revision": 1,
                                    "journal_backend": "arte_typed_journal_v4",
                                    "v4_review_available": False}]}
                            elif f"/runs/{run_id}?compact=true" in url:
                                payload = {"run_id": run_id, "mode": "backtest",
                                    "journal_backend": "arte_typed_journal_v4",
                                    "status": "completed" if state["completed"] else "running",
                                    "runtime_ready": True, "progress": 1 if state["completed"] else .35,
                                    "processed_events": 6809, "current_time": "2026-08-19T06:00:00-04:00",
                                    "session_date": "2026-08-19", "tickers": [],
                                    "execution_scope": {"admitted_ticker_count": 6100},
                                    "preparation_progress": {"completed": 0, "total": 0},
                                    "preparation_stage": "strategy_one_runtime",
                                    "work_progress": {"active": not state["completed"],
                                                      "phase": "strategy_one_runtime"},
                                    "level_book_coverage": {"eligible_ticker_count": 6100,
                                                            "excluded_ticker_count": 0,
                                                            "excluded": []}}
                            elif "/v4-terminal-page" in url:
                                payload = {"schema_version": "strategy-one-v4-terminal-review-page-v1",
                                    "run": {"run_id": run_id, "session_date": "2026-08-19",
                                            "strategy_id": "early-squeeze-strategy",
                                            "strategy_revision": 1},
                                    "status": "completed", "verified_sequence": 2283,
                                    "market_cursor_verified": True,
                                    "market_cursor": {"session_date": "2026-08-19",
                                                      "boundary_ms": 19726200},
                                    "limitations": [], "financial_accounts": {},
                                    "events": [], "next_sequence": 0, "complete": True}
                            elif "/v4-performance" in url:
                                payload = _performance_page()
                            elif "/v4-trade-history" in url:
                                payload = {"fills": [], "commissions": [],
                                           "next_fill_sequence": 0,
                                           "next_commission_sequence": 0,
                                           "complete": True}
                            elif "/v4-order-history" in url:
                                payload = {"commands": [], "transitions": [],
                                           "next_command_sequence": 0,
                                           "next_transition_sequence": 0,
                                           "complete": True}
                            else:
                                payload = {}
                            route.fulfill(json=payload)

                        page.route("**/api/trading/**", handle)
                        page.goto("http://127.0.0.1:5173/#backtest-trading")
                        page.get_by_text("Backtest running").wait_for()
                        self.assertEqual(state["legacy_requests"], [])
                        page.locator(".backtest-v4-running").screenshot(
                            path=str(evidence / f"v4-running-{theme}-{scale}-{width}.png"))
                        state["completed"] = True
                        page.get_by_text("2,283 verified records").wait_for(timeout=10000)
                        self.assertEqual(state["legacy_requests"], [])
                        self.assertEqual(errors, [])
                        page.locator(".backtest-v4-canvas-review").screenshot(
                            path=str(evidence / f"v4-handoff-{theme}-{scale}-{width}.png"))
                    finally:
                        browser.close()
