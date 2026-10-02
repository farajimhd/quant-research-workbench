"""Opt-in browser checks; all backtest mutations are intercepted."""
import os
import unittest


@unittest.skipUnless(os.environ.get("BACKTEST_HISTORY_UI"), "opt-in managed frontend browser test")
class BacktestHistoryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("BACKTEST_REAL_V4_REVIEW"),
                         "opt-in read-only saved-run integration check")
    def test_real_saved_v4_review_opens_from_history(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 900, "height": 700})
                errors = []
                requests = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto("http://127.0.0.1:5173/#backtest-trading")
                table = page.get_by_role("region", name="Recent backtests table", exact=True)
                table.wait_for(timeout=60_000)
                table.locator("tbody tr").filter(has_text="a2c47451").get_by_role(
                    "button", name="Review backtest", exact=False).click()
                review = page.locator(".backtest-v4-canvas-review")
                review.get_by_text("Backtest Canvas · Strategy 1").wait_for(timeout=90_000)
                self.assertIn("backtest_run=", page.url)
                self.assertIn("verified records", review.inner_text())
                self.assertIn("Trading Journal", review.inner_text())
                self.assertIn("Execution Audit", review.inner_text())
                self.assertEqual(page.locator(".backtest-v4-direct-review").count(), 0)
                review.get_by_text("10 episodes", exact=False).first.wait_for(timeout=60_000)
                review.get_by_text("30 verified order commands", exact=False).first.wait_for(timeout=60_000)
                self.assertNotIn("No frontend renderer is registered", review.inner_text())
                self.assertNotIn("Chart unavailable", review.inner_text())
                self.assertFalse(any("/canvas?" in url or "/journal/episodes/" in url
                                     for url in requests))
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    evidence = Path(os.environ["BACKTEST_HISTORY_EVIDENCE"])
                    evidence.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(evidence / "real-strategy-one-review.png"))
                review.get_by_role("tab", name="Positions", exact=False).click()
                review.get_by_placeholder("Search positions, symbols, setups, exits…").wait_for()
                journal = review.locator(".performance-journal")
                self.assertEqual(journal.get_by_role("combobox", name="Filter by All directions").count(), 1)
                self.assertEqual(journal.get_by_role("columnheader", name="entry shares outstanding", exact=False).count(), 1)
                self.assertEqual(journal.get_by_role("columnheader", name="entry last minute trade count", exact=False).count(), 1)
                review.get_by_text("WFF", exact=True).first.wait_for(timeout=60_000)
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    page.screenshot(path=str(evidence / "real-strategy-one-positions.png"))
                with page.expect_popup() as opened:
                    review.get_by_role("button", name="Open WFF Charts & Quotes in a new tab").first.click()
                focus = opened.value
                focus_requests = []
                focus.on("request", lambda request: focus_requests.append(request.url))
                focus.locator(".backtest-v4-chart-focus").wait_for(timeout=60_000)
                self.assertIn("backtest_ticker=WFF", focus.url)
                self.assertIn("Charts & Quotes", focus.locator(".backtest-v4-chart-focus").inner_text())
                charts_quotes = focus.locator('.workspace-window[data-window-kind="charts_quotes"]')
                self.assertEqual(charts_quotes.count(), 1)
                self.assertFalse(charts_quotes.locator(".charts-quotes-tape").is_visible())
                self.assertFalse(charts_quotes.locator(".charts-quotes-context-row").is_visible())
                focus.locator(".backtest-v4-saved-market-state").get_by_text("ms old", exact=False).wait_for(timeout=60_000)
                focus.locator(".chart-shell canvas").first.wait_for(timeout=60_000)
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    focus.screenshot(path=str(evidence / "real-strategy-one-chart-focus-maximized.png"))
                charts_quotes.get_by_role("button", name="Restore chart panels").click()
                self.assertTrue(charts_quotes.locator(".charts-quotes-tape").is_visible())
                self.assertTrue(charts_quotes.locator(".charts-quotes-context-row").is_visible())
                charts_quotes.get_by_text("5s context · certified ARTE").wait_for(timeout=60_000)
                charts_quotes.get_by_text("30s context · certified ARTE").wait_for(timeout=60_000)
                self.assertGreaterEqual(charts_quotes.locator('.charts-quotes-resizer').count(), 2)
                self.assertFalse(any("/canvas-market-events/" in url for url in focus_requests))
                self.assertNotIn("Chart unavailable", focus.locator(".backtest-v4-chart-focus").inner_text())
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    focus.screenshot(path=str(evidence / "real-strategy-one-chart-focus.png"))
                focus.close()
                activity = review.locator(".strategy-activity-surface")
                activity.get_by_role("button", name="Inspect strategy event", exact=False).first.click()
                activity.get_by_role("complementary", name="Strategy event details").wait_for()
                self.assertIn(".", activity.get_by_role("complementary", name="Strategy event details").locator(".market-time-primary").first.inner_text())
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    activity.scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence / "real-strategy-one-activity.png"))
                review.get_by_role("button", name="Expand row").first.click()
                self.assertFalse(any("/journal/episodes/" in url for url in requests))
                review.get_by_role("button", name="Canvas management").click()
                library = review.get_by_role("region", name="Container library")
                with page.expect_response(lambda response: "/v4-chart?" in response.url
                                          and response.status == 200, timeout=60_000):
                    library.locator("article").filter(has_text="Chart").first.get_by_role(
                        "button", name="Add").click()
                review.get_by_role("region", name="Saved WFF chart").wait_for(timeout=60_000)
                self.assertEqual(errors, [])
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get("BACKTEST_REAL_V4_REVIEW"),
                         "opt-in read-only saved-run integration check")
    def test_real_saved_v4_canvas_dark_wide(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1600, "height": 950})
                page.add_init_script("localStorage.setItem('quant-research-workbench.theme', 'dark'); localStorage.setItem('quant-research-workbench.ui-scale', '1.25')")
                page.goto("http://127.0.0.1:5173/#backtest-trading")
                table = page.get_by_role("region", name="Recent backtests table", exact=True)
                table.wait_for(timeout=60_000)
                table.locator("tbody tr").filter(has_text="a2c47451").get_by_role("button", name="Review backtest", exact=False).click()
                review = page.locator(".backtest-v4-canvas-review")
                review.get_by_text("10 episodes", exact=False).first.wait_for(timeout=90_000)
                self.assertIn("Trading Journal", review.inner_text())
                self.assertIn("Position Manager", review.inner_text())
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    evidence = Path(os.environ["BACKTEST_HISTORY_EVIDENCE"])
                    evidence.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(evidence / "real-strategy-one-review-dark-wide.png"))
            finally:
                browser.close()

    def test_numbered_v4_run_does_not_show_legacy_candidate_identity(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 900, "height": 700})
                def handle(route):
                    path = route.request.url.split("?", 1)[0]
                    if path.endswith("/backtest/runs"):
                        route.fulfill(json={"rows": [{
                            "run_id": "strategy-one-run", "created_at": "2026-09-28T12:00:00Z",
                            "status": "completed", "session_date": "2026-08-18",
                            "journal_backend": "arte_typed_journal_v4",
                            "strategy_revision": 1, "configuration_revision": 350,
                            "configuration_label": "Legacy candidate label",
                            "journal_sequence": 2216, "v4_review_available": True,
                        }]})
                    elif path.endswith("/configuration-options"):
                        route.fulfill(json={"candidates": [], "available_run_plans": [],
                                            "error": ""})
                    elif path.endswith("/v4-terminal-page"):
                        route.fulfill(json={
                            "schema_version": "strategy-one-v4-terminal-review-page-v1",
                            "run": {"run_id": "strategy-one-run", "strategy_revision": 1},
                            "status": "completed", "verified_sequence": 2216,
                            "market_cursor": {"session_date": "2026-08-18",
                                              "boundary_ms": 19800000},
                            "market_cursor_verified": True, "limitations": [],
                            "financial_accounts": {}, "events": [],
                            "next_sequence": 0, "complete": True,
                        })
                    elif path.endswith("/v4-trade-history"):
                        route.fulfill(json={
                            "schema_version": "strategy-one-v4-trade-history-page-v1",
                            "fills": [], "commissions": [],
                            "next_fill_sequence": 0, "next_commission_sequence": 0,
                            "complete": True,
                        })
                    elif path.endswith("/v4-performance"):
                        route.fulfill(json={
                            "schema_version": "strategy-one-v4-performance-report-v1",
                            "report": {"schema_version": 2, "episode_definition": "flat_to_flat_position_lifecycle",
                                       "summary": {}, "episodes": [], "equity_curve": [],
                                       "pnl_candles": {"30m": [], "1h": [], "1d": [], "1M": []},
                                       "strategies": [], "execution": {}, "risk": {}, "scope": {}},
                            "position_lifecycles": [], "fill_count": 0, "fee_count": 0,
                        })
                    elif path.endswith("/v4-order-history"):
                        route.fulfill(json={
                            "schema_version": "strategy-one-v4-order-history-page-v1",
                            "commands": [], "transitions": [],
                            "next_command_sequence": 0,
                            "next_transition_sequence": 0, "complete": True,
                        })
                    else:
                        route.fulfill(json={"items": [], "checks": []})
                page.route("**/api/trading/**", handle)
                page.goto("http://127.0.0.1:5173/#backtest-trading")
                table = page.get_by_role("region", name="Recent backtests table", exact=True)
                table.wait_for()
                self.assertIn("Strategy 1", table.locator(".backtest-strategy-group").inner_text())
                table.get_by_role("button", name="Show sessions for", exact=False).first.click()
                self.assertEqual(table.locator(".backtest-session-rows:visible th strong").inner_text(), "strategy")
                self.assertNotIn("Candidate", table.inner_text())
                if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                    evidence = Path(os.environ["BACKTEST_HISTORY_EVIDENCE"])
                    evidence.mkdir(parents=True, exist_ok=True)
                    table.scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence / "strategy-one-history.png"))
                page.get_by_role("button", name="Review backtest strategy", exact=True).click()
                page.get_by_text("Backtest Canvas · Strategy 1").wait_for()
                self.assertIn("backtest_run=strategy-one-run", page.url)
                self.assertEqual(page.locator(".backtest-v4-canvas-review").count(), 1)
                self.assertEqual(page.locator(".backtest-v4-direct-review").count(), 0)
            finally:
                browser.close()

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
                    for scale in (.8, 1, 1.25):
                        blocked = "configuration-options"
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
                                table.get_by_role("button", name="Show sessions for", exact=False).first.click()
                                # Readiness has its own debounce after indicator warmup.
                                for _ in range(50):
                                    if held:
                                        break
                                    page.wait_for_timeout(100)
                                self.assertTrue(held, f'{blocked} was not requested')
                                self.assertEqual(len(reads), 1, 'Development mount must not issue duplicate history reads')
                                self.assertTrue(page.get_by_role('button', name='Run Full-market Backtest', exact=True).is_disabled())
                                with page.expect_response(lambda response: response.url.split('?')[0].endswith('/api/trading/backtest/runs')):
                                    page.get_by_role('button', name='Refresh runs', exact=True).click()
                                page.wait_for_function("document.querySelector('.backtest-run-history').getAttribute('aria-busy') === 'false'")
                                self.assertEqual(len(reads), 2)
                                table.get_by_role("button", name="Show sessions for", exact=False).first.click()
                                page.get_by_role('button', name='Resume backtest saved-01', exact=True).click()
                                table.get_by_role('alert').filter(has_text='Test intercepted resume').wait_for()
                                self.assertEqual(len(mutations), 1)
                                if output:
                                    page.screenshot(path=str(output / f'{theme}-{scale}-{width}-pending.png'))
                                for route in held:
                                    route.fulfill(status=503, json={"detail": "Setup temporarily unavailable"})
                                page.wait_for_timeout(100)
                                self.assertEqual(table.locator('.backtest-session-rows:visible tr').count(), 1)
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
                while table.get_by_role("button", name="Show sessions for", exact=False).count():
                    table.get_by_role("button", name="Show sessions for", exact=False).first.click()
                # Wheel input must reach history inside the clipped Canvas shell.
                # scroll_into_view/click auto-scrolling can hide an overflow:hidden bug.
                setup = page.locator(".mode-launch-page")
                self.assertEqual(setup.evaluate("el => getComputedStyle(el).overflowY"), "auto")
                surface = page.locator(".mode-launch-surface")
                self.assertGreater(surface.bounding_box()["height"], 300)
                self.assertTrue(page.get_by_role("button", name="Run Full-market Backtest", exact=True).is_visible())
                page.mouse.move(500, 350)
                page.mouse.wheel(0, 10000)
                page.wait_for_function("document.querySelector('.mode-launch-page').scrollTop > 0")
                footer = page.get_by_role("button", name="Older", exact=True)
                page.wait_for_function("document.querySelector('.backtest-run-history footer').getBoundingClientRect().bottom <= innerHeight")
                self.assertTrue(footer.is_visible())
                self.assertEqual(table.locator(".backtest-session-rows:visible tr").count(), 10)
                self.assertEqual(table.locator(".backtest-session-rows:visible th strong").all_text_contents(),
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
                while table.get_by_role("button", name="Show sessions for", exact=False).count():
                    table.get_by_role("button", name="Show sessions for", exact=False).first.click()
                self.assertEqual(table.locator(".backtest-session-rows:visible tr").count(), 2)
                page.get_by_role("button", name="Newer", exact=True).click()
                page.get_by_role("button", name="Review backtest run-0009", exact=True).click()
                page.wait_for_url("**backtest_run=run-0009#backtest-trading")
                self.assertEqual(len(mutations), 2)
            finally:
                browser.close()

    def test_strategy_session_performance_groups(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright
        rows = [dict(run_id=f"saved-{i}", created_at=f"2026-09-{10-i:02d}T12:00:00Z",
                     session_date="2026-08-21" if i < 2 else "2026-08-24", status="completed",
                     strategy_id="fixed", strategy_revision=34, configuration_content_hash="a" * 64,
                     comparison_group_key="a" * 64, initial_cash=10000, tickers=[],
                     journal_backend="arte_typed_journal_v4", journal_sequence=10,
                     v4_review_available=True, resident=False) for i in range(5)]
        rows[3].update(status="failed")
        # Same strategy number, distinct frozen parameters, separate card.
        rows[4].update(configuration_content_hash="b" * 64, comparison_group_key="b" * 64)
        summaries = [dict(net_pnl=100, episode_count=2, win_count=1, win_rate="0.5", total_fees=2),
                     dict(net_pnl=999, episode_count=9, win_count=9, win_rate=1, total_fees=9),
                     dict(net_pnl=-40, episode_count=3, win_count=1, win_rate="0.333333", total_fees=3)]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for theme in ("light", "dark"):
                    for scale in (0.8, 1, 1.25):
                        for width in (1440, 900):
                            page = browser.new_page(viewport={"width": width, "height": 900})
                            page.add_init_script(f"localStorage.setItem('quant-research-workbench.theme', '{theme}');localStorage.setItem('quant-research-workbench.ui-scale', '{scale}')")
                            errors = []
                            page.on("pageerror", lambda e: errors.append(str(e)))
                            def handle(route):
                                path = route.request.url.split("?")[0]
                                if route.request.method != "GET":
                                    route.fulfill(status=409, json={"detail": "Mutation blocked"})
                                elif path.endswith("/backtest/runs"):
                                    route.fulfill(json={"rows": rows})
                                elif path.endswith("/history-performance"):
                                    route.fulfill(json={"rows": [dict(run_id=f"saved-{i}",
                                        verified_sequence=10, status="available", report={"summary": summaries[i]})
                                        if i < 3 else dict(run_id=f"saved-{i}", status="unavailable", error="Missing journal evidence")
                                        for i in range(5)]})
                                elif path.endswith("/configuration-options"):
                                    route.fulfill(json={"candidates": [], "available_run_plans": [], "error": ""})
                                else:
                                    route.fulfill(json={"items": [], "checks": []})
                            page.route("**/api/trading/**", handle)
                            page.goto("http://127.0.0.1:5173/#backtest-trading")
                            history = page.locator(".backtest-run-history")
                            table = page.get_by_role("region", name="Recent backtests table", exact=True)
                            table.get_by_text("$60.00", exact=True).wait_for()
                            self.assertEqual(history.locator("table").count(), 1)
                            self.assertEqual(page.locator(".backtest-history-note").count(), 0)
                            first = table.locator(".backtest-strategy-group").first
                            self.assertIn("2 / 2 completed sessions", first.inner_text())
                            self.assertIn("40%", first.inner_text())
                            self.assertEqual(table.locator(".backtest-strategy-group").count(), 2)
                            self.assertEqual(table.locator(".backtest-session-rows:visible").count(), 0)
                            diagnostics = history.locator("footer .backtest-performance-errors")
                            self.assertEqual(diagnostics.locator("li").count(), 1)
                            self.assertIn("2 runs", diagnostics.locator("summary").inner_text())
                            self.assertFalse(diagnostics.evaluate("el => el.open"))
                            diagnostics.locator("summary").focus()
                            page.keyboard.press("Enter")
                            self.assertIn("Missing journal evidence (2 runs)", diagnostics.inner_text())
                            if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                                output = Path(os.environ["BACKTEST_HISTORY_EVIDENCE"])
                                output.mkdir(parents=True, exist_ok=True)
                                history.locator("footer").screenshot(path=str(output / f"footer-{theme}-{scale}-{width}.png"))
                            diagnostics.locator("summary").click()
                            first.locator(".backtest-group-toggle").focus()
                            page.keyboard.press("Enter")
                            sessions = table.locator(".backtest-session-rows:visible")
                            self.assertEqual(sessions.locator("tr").count(), 4)
                            self.assertEqual(sessions.locator("th strong").all_text_contents(), [f"saved-{i}" for i in range(4)])
                            self.assertTrue(sessions.get_by_role("button", name="Resume backtest saved-0", exact=True).is_disabled())
                            self.assertTrue(sessions.get_by_role("button", name="Review backtest saved-0", exact=True).is_enabled())
                            self.assertFalse(errors)
                            self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                            if os.environ.get("BACKTEST_HISTORY_EVIDENCE"):
                                table.evaluate("el => el.scrollLeft = 0")
                                history.screenshot(path=str(output / f"performance-{theme}-{scale}-{width}.png"))
                            first.locator(".backtest-group-toggle").click()
                            self.assertEqual(table.locator(".backtest-session-rows:visible").count(), 0)
                            page.close()
            finally:
                browser.close()
