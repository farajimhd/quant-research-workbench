"""Opt-in recovery interactions against the managed frontend; no run mutations."""
import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('BACKTEST_REVIEW_UI'), 'opt-in managed browser check')
class BacktestReviewUITests(unittest.TestCase):
    def test_centered_loading_error_and_retry(self):
        from playwright.sync_api import sync_playwright
        output = Path(os.environ['BACKTEST_REVIEW_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for theme in ('light', 'dark'):
                    for scale in (.8, 1, 1.25):
                        for width, height in ((1600, 1000), (900, 700)):
                            with self.subTest(theme=theme, scale=scale, width=width):
                                context = browser.new_context(viewport=dict(width=width, height=height))
                                context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}')")
                                page = context.new_page()
                                held, requests = [], []
                                def handle(route):
                                    url = route.request.url
                                    if '/runs/saved-review/review' in url:
                                        requests.append(url)
                                        held.append(route)
                                    elif '/runs/saved-review' in url:
                                        self.assertIn('compact=true', url)
                                        route.fulfill(status=404, json={'detail': 'Not resident'})
                                    elif url.endswith('/backtest/runs'):
                                        route.fulfill(json={'rows': [{'run_id': 'saved-review', 'status': 'stopped'}]})
                                    else:
                                        route.fulfill(json={})
                                page.route('**/api/trading/**', handle)
                                page.goto('http://127.0.0.1:5173/?backtest_run=saved-review#backtest-trading')
                                loading = page.get_by_role('status').filter(has_text='Opening backtest')
                                loading.wait_for()
                                page.locator('.backtest-recovery-state .loading-spinner').wait_for()
                                for _ in range(100):
                                    if held: break
                                    page.wait_for_timeout(50)
                                self.assertTrue(held)
                                self.assertTrue(all('compact=true' in url for url in requests))
                                bounds = page.locator('.backtest-recovery-state').bounding_box()
                                content = page.locator('.backtest-recovery-content').bounding_box()
                                self.assertLess(abs(content['y'] + content['height']/2 - bounds['y'] - bounds['height']/2), 3)
                                prefix = output / f'{theme}-{scale}-{width}'
                                page.screenshot(path=str(prefix) + '-loading.png')
                                for route in held[:]: route.fulfill(status=503, json={'detail': 'Saved evidence is temporarily unavailable.'})
                                held.clear()
                                page.get_by_role('heading', name='Could not open backtest').wait_for()
                                retry = page.get_by_role('button', name='Retry connection')
                                setup = page.get_by_role('button', name='Return to setup')
                                a, b = retry.bounding_box(), setup.bounding_box()
                                self.assertGreaterEqual(b['x'] - a['x'] - a['width'], 6 * scale)
                                page.screenshot(path=str(prefix) + '-error.png')
                                retry.click()
                                loading.wait_for()
                                setup.click()
                                page.wait_for_url('**/#backtest-trading')
                                self.assertNotIn('backtest_run', page.url)
                                context.close()
            finally:
                browser.close()
