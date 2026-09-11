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
    def test_batch_launch_submits_the_selected_period(self) -> None:
        from playwright.sync_api import sync_playwright
        base = os.environ.get("BACKTEST_RECOVERY_URL", "http://127.0.0.1:5173").rstrip("/")
        submitted = []
        plan = dict(run_plan_id='plan',name='Test plan',profile_id='profile',strategy_id='strategy',strategy_revision=47)
        def handle(route):
            path = route.request.url.split('?')[0]
            if path.endswith('/structure-books'):
                data = dict(items=[dict(id=t,ticker=t,version='causal-swing-closing-book-6',start='2026-08-01',end='2026-08-31') for t in ('SUGP','JUNS')])
            elif path.endswith('/configuration-options'):
                data = dict(candidate_id='candidate',run_plan_id='plan',available_run_plans=[plan],error='',candidates=[dict(candidate_id='candidate',candidate_revision=192,label='Test',content_hash='hash')])
            elif path.endswith('/indicator-warmup'):
                data = dict(status='ready',items=[],ready_count=2,ticker_count=2,tickers=['SUGP','JUNS'])
            elif path.endswith('/historical-preflight'):
                data = dict(strategy_run_ready=True,configuration_revision_id='candidate',run_plan_id='plan',configuration_revision=192,checks=[],window=dict(sessions=['2026-08-21']))
            elif path.endswith('/backtest/runs') and route.request.method=='POST':
                submitted.append(route.request.post_data_json)
                route.fulfill(status=409,json=dict(detail='Intercepted launch for browser test'))
                return
            else:
                route.continue_()
                return
            route.fulfill(json=data)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/api/trading/**',handle)
                page.goto(base+'/#backtest-trading')
                page.get_by_role('button',name='Ticker preset',exact=True).click()
                page.get_by_role('option',name='SUGP and JUNS',exact=True).click()
                page.get_by_role('button',name='Time period',exact=True).click()
                page.get_by_role('option',name='After hours · 16:00–20:00 ET',exact=True).click()
                page.get_by_role('button',name='Run 2 Backtests',exact=True).click(timeout=30000)
                page.get_by_text('Intercepted launch for browser test',exact=True).wait_for()
                self.assertEqual(len(submitted),1)
                self.assertEqual(submitted[0]['start_time'],'16:00:00')
                self.assertEqual(submitted[0]['end_time'],'20:00:00')
                self.assertEqual(submitted[0]['tickers'],['SUGP'])
            finally:
                browser.close()

    def test_existing_run_does_not_launch_setup_requests(self) -> None:
        from playwright.sync_api import sync_playwright
        base = os.environ.get("BACKTEST_RECOVERY_URL", "http://127.0.0.1:5173").rstrip("/")
        run_id = os.environ["BACKTEST_RECOVERY_RUN_ID"]
        setup_requests = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                def block_setup(route):
                    setup_requests.append(route.request.url)
                    route.fulfill(status=409, json={"detail": "Monitoring must not launch setup work"})
                for endpoint in ("backtest/configuration-options", "backtest/indicator-warmup",
                                 "historical-preflight", "backtest/structure-books"):
                    page.route(f"**/api/trading/{endpoint}*", block_setup)
                page.goto(f"{base}/?backtest_run={run_id}#backtest-trading")
                page.get_by_role("button", name="Load next 2,000 older events").wait_for(timeout=60000)
                page.wait_for_timeout(2000)
                self.assertEqual(setup_requests, [])
            finally:
                browser.close()

    def test_failed_run_reports_cause_without_review_or_retry(self) -> None:
        from playwright.sync_api import sync_playwright

        base = os.environ.get("BACKTEST_RECOVERY_URL", "http://127.0.0.1:5173").rstrip("/")
        run_id = os.environ["BACKTEST_RECOVERY_RUN_ID"]
        path = f"/api/trading/backtest/runs/{run_id}"
        failure = {"run_id": run_id, "status": "failed", "error": "Canonical 1s warm-up required"}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for resident in (False, True):
                    for route_name in ("backtest-trading", "canvas-focus"):
                        with self.subTest(resident=resident, route=route_name):
                            context = browser.new_context()
                            mutations = []
                            def handle(route):
                                url = route.request.url.split("?")[0]
                                if route.request.method != "GET":
                                    mutations.append(url)
                                    route.fulfill(status=409, json={"detail": "No mutations allowed"})
                                elif url.endswith(path):
                                    route.fulfill(status=200 if resident else 404, json=failure if resident else {"detail": "Not resident"})
                                elif url.endswith("/api/trading/backtest/runs"):
                                    route.fulfill(json={"rows": [failure]})
                                else:
                                    route.continue_()
                            context.route("**/api/trading/backtest/runs**", handle)
                            page = context.new_page()
                            key = "backtest_run" if route_name == "backtest-trading" else "replay_run"
                            page.goto(f"{base}/?{key}={run_id}&historical_mode=backtest#{route_name}")
                            page.get_by_text("Backtest failed", exact=True).wait_for()
                            self.assertEqual(page.get_by_role("button", name="Retry connection").count(), 0)
                            page.get_by_text("Original failure details", exact=True).click()
                            page.get_by_text("Canonical 1s warm-up required", exact=False).wait_for()
                            page.get_by_role("button", name="Return to setup", exact=True).click()
                            page.wait_for_function("!new URL(location.href).searchParams.has(\"backtest_run\") && !new URL(location.href).searchParams.has(\"replay_run\")")
                            self.assertIsNone(page.evaluate("sessionStorage.getItem('backtest.active-run.v1')"))
                            self.assertEqual(mutations, [])
                            context.close()
            finally:
                browser.close()

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
