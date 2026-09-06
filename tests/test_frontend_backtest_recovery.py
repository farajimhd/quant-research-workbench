"""Opt-in browser regression against a saved run; never starts an execution.

Set BACKTEST_RECOVERY_RUN_ID and BACKTEST_RECOVERY_URL (managed frontend URL).
All run-status and Canvas responses are replayed into an isolated browser so
running/completed/error states can be exercised without commanding the engine.
"""
from __future__ import annotations

import json
import os
import re
import unittest
from urllib.request import urlopen
from urllib.parse import quote


@unittest.skipUnless(os.environ.get("BACKTEST_RECOVERY_RUN_ID"), "opt-in browser regression")
class BacktestRecoveryBrowserTests(unittest.TestCase):
    def test_reload_polling_paging_and_connection_failure(self) -> None:
        from playwright.sync_api import sync_playwright

        base = os.environ.get("BACKTEST_RECOVERY_URL", "http://127.0.0.1:5173").rstrip("/")
        run_id = os.environ["BACKTEST_RECOVERY_RUN_ID"]
        path = f"/api/trading/backtest/runs/{run_id}"
        with urlopen(base + path, timeout=60) as response:
            saved_run = json.load(response)
        ticker = saved_run["tickers"][0]
        with urlopen(base + path + "/canvas?symbol=" + quote(ticker), timeout=60) as response:
            saved_canvas = json.load(response)
        self.assertFalse(saved_canvas["trading"]["strategy_activity_page"]["complete"],
                         "Choose a saved run exceeding the 2,000-row journal page")
        state = {"status": "running", "revision": 0, "unavailable": False}
        reads: list[str] = []
        mutations: list[str] = []
        errors: list[str] = []

        def handle(route):
            request = route.request
            url = request.url
            if request.method != "GET":
                mutations.append(url)
                route.fulfill(status=409, json={"detail": "Execution mutations forbidden in this review"})
            elif re.search(re.escape(path) + r"(?:\?.*)?$", url):
                if state["unavailable"]:
                    route.fulfill(status=503, json={"detail": "Test connection unavailable"})
                    return
                data = {**saved_run, "status": state["status"], "updated_at": str(state["revision"]), "progress": .71}
                if "compact=true" in url:
                    data.pop("canvas_profile", None)
                route.fulfill(json=data)
            elif path + "/canvas" in url:
                reads.append("canvas")
                data = {**saved_canvas, "run": {**saved_run, "status": state["status"]}}
                route.fulfill(json=data)
            elif "/strategy-activity?" in url:
                reads.append("older")
                route.continue_()
            else:
                route.continue_()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/trading/**", handle)
                target = f"{base}/?backtest_run={run_id}#backtest-trading"
                page.goto(target)
                page.get_by_role("progressbar").wait_for()
                page.get_by_role("button", name="Load next 2,000 older events").wait_for()
                page.wait_for_timeout(3500)
                self.assertEqual(reads.count("older"), 0, "Running Canvas must not eagerly reread old history")
                count = reads.count("canvas")
                page.wait_for_timeout(2500)
                self.assertEqual(reads.count("canvas"), count, "Unchanged revision must not rebuild the Canvas")
                state["revision"] = 1
                page.wait_for_timeout(2500)
                self.assertEqual(reads.count("canvas"), count + 1)
                page.reload()
                page.get_by_role("progressbar").wait_for()
                self.assertIn(f"backtest_run={run_id}", page.url)
                state["status"] = "completed"
                state["revision"] = 2
                page.get_by_text(re.compile(r"^Backtest completed$", re.I)).wait_for()
                page.wait_for_timeout(4000)
                self.assertGreater(reads.count("older"), 0, "Completed review still loads full history")
                page.get_by_role("button", name="Return to Backtest setup").click()
                self.assertNotIn("backtest_run=", page.url)
                self.assertIsNone(page.evaluate("sessionStorage.getItem('backtest.active-run.v1')"))
                state["unavailable"] = True
                page.goto(target)
                page.get_by_role("button", name="Retry connection").wait_for()
                self.assertEqual(page.get_by_role("progressbar").count(), 0)
                state["unavailable"] = False
                page.get_by_role("button", name="Retry connection").click()
                page.get_by_role("progressbar").wait_for()
                self.assertEqual(mutations, [], "Recovery must not create, resume, or command any run")
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
