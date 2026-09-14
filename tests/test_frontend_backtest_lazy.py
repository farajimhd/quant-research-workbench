"""Exercise accelerated status updates without starting or resuming a run."""
import copy
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen


@unittest.skipUnless(os.environ.get('BACKTEST_LAZY_UI'), 'opt-in managed browser test')
class LazyBacktestUITests(unittest.TestCase):
    def test_held_view_paging_follow_and_evidence(self):
        from playwright.sync_api import sync_playwright
        run_id = os.environ['BACKTEST_RECOVERY_RUN_ID']
        base = f'http://127.0.0.1:8000/api/trading/backtest/runs/{run_id}'
        with urlopen(base + '?compact=true') as response: run = json.load(response)
        with urlopen(base + '/canvas?symbol=AAPL&lazy=true&include_chart=false') as response: canvas = json.load(response)
        output = Path(os.environ['BACKTEST_REVIEW_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width': 1600, 'height': 1000})
                page = context.new_page()
                requests, evidence = [], []
                progress = {}
                clock = datetime.fromisoformat(run['current_time'])
                def row(n):
                    event_time = (datetime.fromisoformat(run['current_time']) - timedelta(seconds=n)).isoformat()
                    return dict(record_id=f'event-{n}', sequence=1000-n, run_id=run_id, ticker='AAPL',
                        strategy_id='test', event_type='decision', action='wait', reason=f'causal reason {n}',
                        event_time=event_time, detected_at=event_time, event_evidence={})
                def route_request(route):
                    from urllib.parse import urlparse, parse_qs
                    nonlocal clock
                    url = route.request.url
                    path = urlparse(url).path
                    query = parse_qs(urlparse(url).query)
                    requests.append((path, query))
                    if path.endswith('/canvas'):
                        self.assertEqual(query.get('lazy'), ['true'])
                        value = copy.deepcopy(canvas)
                        value['run'].update(status='running', current_time=clock.isoformat(), updated_at=clock.isoformat())
                        value['trading'].update(presentation_as_of=clock.isoformat(), presentation_sequence=999)
                        route.fulfill(json=value)
                    elif path.endswith('/strategy-activity'):
                        if 'record_id' in query:
                            evidence.append(query['record_id'][0])
                            route.fulfill(json={'rows': [row(int(query['record_id'][0].split('-')[1]))], 'complete': True, 'as_of': query['as_of'][0]})
                        else:
                            self.assertEqual(query['limit'], ['200'])
                            offset = int(query.get('offset', ['0'])[0])
                            route.fulfill(json={'rows': [row(n) for n in range(offset, offset + 200)], 'complete': offset >= 400,
                                'next_offset': offset + 200, 'as_of': query['as_of'][0], 'presentation_sequence': 999})
                    elif path.endswith('/' + run_id):
                        clock += timedelta(seconds=30)
                        route.fulfill(json={**run, 'status': 'running', 'current_time': clock.isoformat(), 'updated_at': clock.isoformat(), **progress})
                    elif '/ticker-presentations' in path:
                        route.fulfill(json={'rows': []})
                    else:
                        route.fulfill(status=409, json={'detail': 'Unexpected test request'})
                page.route('**/api/trading/**', route_request)
                page.goto(f'http://127.0.0.1:5173/?backtest_run={run_id}#backtest-trading')
                page.locator('.performance-journal').wait_for()
                self.assertEqual(page.locator('[aria-label="Backtest view updates"]').count(), 0)
                count_before = len([p for p, _ in requests if p.endswith('/canvas')])
                page.wait_for_timeout(5500)
                self.assertGreater(len([p for p, _ in requests if p.endswith('/canvas')]), count_before)
                page.get_by_text('Strategy Activity', exact=True).first.scroll_into_view_if_needed()
                activity = page.get_by_role('region', name='Strategy activity', exact=True)
                activity.wait_for()
                activity.get_by_role('row').filter(has_text='causal reason 0').click()
                page.get_by_role('complementary', name='Strategy event details').wait_for()
                page.wait_for_timeout(500)
                initial = len([p for p, _ in requests if p.endswith('/canvas')])
                page.wait_for_timeout(6500)
                self.assertGreater(len([p for p, _ in requests if p.endswith('/canvas')]), initial)
                self.assertEqual(page.locator('[aria-label="Backtest view updates"]').count(), 0)
                self.assertEqual(evidence, ['event-0'])
                self.assertEqual(activity.locator('tr[aria-selected="true"]').count(), 1)
                first_page_requests = len([q for p, q in requests if p.endswith('/strategy-activity') and q.get('offset') == ['0']])
                page.get_by_role('button', name='Older events', exact=True).click()
                activity.get_by_role('row').filter(has_text='causal reason 200').wait_for()
                older = [q for p, q in requests if p.endswith('/strategy-activity') and q.get('offset') == ['200']]
                self.assertEqual(older[-1]['through_sequence'], ['999'])
                page.wait_for_timeout(5500)
                activity.get_by_role('row').filter(has_text='causal reason 200').wait_for()
                self.assertEqual(page.locator('[aria-label="Backtest view updates"]').count(), 0)
                page.get_by_role('button', name='Newer events', exact=True).click()
                activity.get_by_role('row').filter(has_text='causal reason 0').wait_for()
                self.assertEqual(len([q for p, q in requests if p.endswith('/strategy-activity') and q.get('offset') == ['0']]), first_page_requests)
                page.get_by_role('button', name='Latest events', exact=True).click()
                page.wait_for_timeout(5500)
                self.assertGreater(len([p for p, _ in requests if p.endswith('/canvas')]), initial)
                activity.get_by_role('row').filter(has_text='causal reason 0').click()
                self.assertEqual(page.locator('[aria-label="Backtest view updates"]').count(), 0)
                held_count = len([p for p, _ in requests if p.endswith('/canvas')])
                page.wait_for_timeout(5500)
                self.assertGreater(len([p for p, _ in requests if p.endswith('/canvas')]), held_count)
                activity.locator('select').nth(2).select_option('AAPL')
                page.wait_for_timeout(600)
                self.assertTrue(any(q.get('ticker') == ['AAPL'] and q.get('offset') == ['0'] for p, q in requests if p.endswith('/strategy-activity')))
                self.assertFalse(any(p.endswith('/results') or p.endswith('/comparison') for p, _ in requests))
                page.screenshot(path=str(output / 'held-selected.png'))
                for phase, label in [('checkpoint_capture', 'Capturing checkpoint'), ('checkpoint_persist', 'Saving checkpoint')]:
                    progress.update(work_progress={'phase': phase, 'active': True, 'elapsed_seconds': 23})
                    page.get_by_text(label, exact=True).wait_for()
                    bar = page.get_by_role('progressbar', name='Backtest progress', exact=True)
                    self.assertIsNone(bar.get_attribute('aria-valuenow'))
                    page.screenshot(path=str(output / f'{phase}.png'))
                progress.update(runtime_ready=False, preparation_stage='level_book_working_set', preparation_progress={'completed': 2432, 'total': 2610}, work_progress={'phase': 'level_book_working_set', 'active': True})
                page.get_by_text('Preparing backtest', exact=True).wait_for()
                self.assertEqual(page.get_by_role('progressbar', name='Backtest warm-up progress', exact=True).get_attribute('aria-valuenow'), '93')
                page.screenshot(path=str(output / 'resumed-warmup.png'))
                progress.update(runtime_ready=True, created_at='2026-09-14T23:00:00+00:00', work_progress={'phase': 'playback', 'active': True})
                page.wait_for_timeout(1500)
                self.assertEqual(page.locator('[aria-label="Backtest view updates"]').count(), 0)
                context.close()
            finally:
                browser.close()
