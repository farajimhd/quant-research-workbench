"""Opt-in browser checks; all backtest mutations are intercepted."""
import os
import unittest


@unittest.skipUnless(os.environ.get("BACKTEST_HISTORY_UI"), "opt-in managed frontend browser test")
class BacktestHistoryTests(unittest.TestCase):
    def test_history_controls_work_while_setup_requests_are_pending(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        output = Path(os.environ["BACKTEST_HISTORY_EVIDENCE"]) if os.environ.get("BACKTEST_HISTORY_EVIDENCE") else None
        if output:
            output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for theme in ("light", "dark"):
                    for scale, blocked in zip((.8, 1, 1.25), ("configuration-options", "indicator-warmup", "historical-preflight")):
                        for width, height in ((1600, 1000), (900, 700)):
                            with self.subTest(theme=theme, scale=scale, width=width, blocked=blocked):
                                context = browser.new_context(viewport={"width": width, "height": height})
                                page = context.new_page()
                                page.add_init_script(f"localStorage.setItem('quant-research-workbench.theme', '{theme}'); localStorage.setItem('quant-research-workbench.ui-scale', '{scale}')")
                                held, reads, mutations = [], [], []
                                def handle(route):
                                    path = route.request.url.split("?")[0]
                                    if path.endswith('/' + blocked):
                                        held.append(route)
                                    elif path.endswith('/backtest/runs') and route.request.method == 'GET':
                                        reads.append(path)
                                        route.fulfill(json={"rows": [dict(run_id="saved-01", created_at="2026-09-14T12:00:00Z", status="stopped", resident=False, session_date="2026-08-21", checkpoint={"resume_supported": True})]})
                                    elif path.endswith('/configuration-options'):
                                        route.fulfill(json=dict(candidate_id='fixture', run_plan_id='plan', error='', candidates=[dict(candidate_id='fixture', candidate_revision=222, label='Test')], available_run_plans=[dict(run_plan_id='plan', profile_id='test', name='Test', strategy_id='test', strategy_revision=47)]))
                                    elif path.endswith('/indicator-warmup'):
                                        route.fulfill(json=dict(status='ready', items=[], ready_count=1, ticker_count=1))
                                    elif path.endswith('/resume'):
                                        mutations.append(path)
                                        route.fulfill(status=409, json={"detail": "Test intercepted resume"})
                                    elif route.request.method != 'GET':
                                        route.fulfill(status=409, json={"detail": "Test blocked mutation"})
                                    else:
                                        route.fulfill(json={"items": [], "checks": []})
                                page.route('**/api/trading/**', handle)
                                page.goto('http://127.0.0.1:5173/#backtest-trading')
                                table = page.get_by_role('region', name='Recent backtests table', exact=True)
                                table.wait_for(timeout=5000)
                                # Readiness has its own debounce after indicator warmup.
                                for _ in range(50):
                                    if held:
                                        break
                                    page.wait_for_timeout(100)
                                self.assertTrue(held, f'{blocked} was not requested')
                                self.assertEqual(len(reads), 1, 'Development mount must not issue duplicate history reads')
                                self.assertTrue(page.get_by_role('button', name='Run Backtest', exact=True).is_disabled())
                                with page.expect_response(lambda response: response.url.endswith('/api/trading/backtest/runs')):
                                    page.get_by_role('button', name='Refresh runs', exact=True).click()
                                page.wait_for_function("document.querySelector('.backtest-run-history').getAttribute('aria-busy') === 'false'")
                                self.assertEqual(len(reads), 2)
                                page.get_by_role('button', name='Resume backtest saved-01', exact=True).click()
                                table.get_by_role('alert').filter(has_text='Test intercepted resume').wait_for()
                                self.assertEqual(len(mutations), 1)
                                if output:
                                    page.screenshot(path=str(output / f'{theme}-{scale}-{width}-pending.png'))
                                for route in held:
                                    route.fulfill(status=503, json={"detail": "Setup temporarily unavailable"})
                                page.wait_for_timeout(100)
                                self.assertEqual(table.locator('tbody tr').count(), 1)
                                context.close()
            finally:
                browser.close()

    def test_history_order_pagination_and_resume_contracts(self):
        from playwright.sync_api import sync_playwright

        rows = [dict(run_id=f"run-{i:04d}", created_at=f"2026-09-{i + 1:02d}T12:00:00Z",
                     current_time="2026-08-21T08:00:05Z", session_date="2026-08-21",
                     status="stopped", resident=False, processed_events=i,
                     checkpoint=dict(resume_supported=True, processed_events=i)) for i in range(12)]
        rows[11]["status"] = "completed"
        rows[10]["checkpoint"]["resume_supported"] = False
        rows[8].update(status="paused", resident=True)
        mutations = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_viewport_size({"width": 900, "height": 700})
                page.add_init_script("localStorage.setItem('quant-research-workbench.ui-scale', '1.25')")
                def handle(route):
                    path = route.request.url.split("?")[0]
                    if route.request.method != "GET":
                        if "/backtest/runs/" in path:
                            mutations.append((path, route.request.post_data_json))
                        route.fulfill(status=409, json={"detail": "Checkpoint rejected by backend"})
                    elif path.endswith("/backtest/runs"):
                        route.fulfill(json={"rows": rows})
                    elif path.endswith("/configuration-options"):
                        route.fulfill(json={"candidates": [], "available_run_plans": [], "error": ""})
                    else:
                        route.fulfill(json={"items": [], "checks": []})
                page.route("**/api/trading/**", handle)
                page.goto("http://127.0.0.1:5173/#backtest-trading")
                table = page.get_by_role("region", name="Recent backtests table", exact=True)
                table.wait_for()
                # Wheel input must reach history inside the clipped Canvas shell.
                # scroll_into_view/click auto-scrolling can hide an overflow:hidden bug.
                setup = page.locator(".mode-launch-page")
                self.assertEqual(setup.evaluate("el => getComputedStyle(el).overflowY"), "auto")
                surface = page.locator(".mode-launch-surface")
                self.assertGreater(surface.bounding_box()["height"], 300)
                self.assertTrue(page.get_by_role("button", name="Test Candidate", exact=True).is_visible())
                page.mouse.move(500, 350)
                page.mouse.wheel(0, 10000)
                page.wait_for_function("document.querySelector('.mode-launch-page').scrollTop > 0")
                footer = page.get_by_role("button", name="Older", exact=True)
                page.wait_for_function("document.querySelector('.backtest-run-history footer').getBoundingClientRect().bottom <= innerHeight")
                self.assertTrue(footer.is_visible())
                self.assertEqual(table.locator("tbody tr").count(), 10)
                self.assertEqual(table.locator("tbody tr td:nth-child(2) strong").all_text_contents(),
                                 [f"run-{i:04d}" for i in range(11, 1, -1)])
                self.assertTrue(page.get_by_role("button", name="Resume backtest run-0011", exact=True).is_disabled())
                self.assertTrue(page.get_by_role("button", name="Resume backtest run-0010", exact=True).is_disabled())
                bounds = page.get_by_role("button", name="Resume backtest run-0009", exact=True).bounding_box()
                self.assertLessEqual(bounds["x"] + bounds["width"], 900)
                page.get_by_role("button", name="Resume backtest run-0009", exact=True).click()
                table.get_by_role("alert").filter(has_text="Checkpoint rejected by backend").wait_for()
                self.assertTrue(mutations[-1][0].endswith("/run-0009/resume"))
                page.get_by_role("button", name="Resume backtest run-0008", exact=True).click()
                table.get_by_role("alert").filter(has_text="Checkpoint rejected by backend").wait_for()
                self.assertTrue(mutations[-1][0].endswith("/run-0008/commands"))
                self.assertEqual(mutations[-1][1], {"command": "play"})
                page.get_by_role("button", name="Older", exact=True).click()
                self.assertEqual(table.locator("tbody tr").count(), 2)
                page.get_by_role("button", name="Newer", exact=True).click()
                page.get_by_role("button", name="Review backtest run-0009", exact=True).click()
                page.wait_for_url("**backtest_run=run-0009#backtest-trading")
                self.assertEqual(len(mutations), 2)
            finally:
                browser.close()
