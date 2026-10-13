"""Rendered programme setup QA with no HTTP service or actual backtest launch."""
import argparse
from datetime import date, timedelta
from itertools import product
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

from ui_review import ensure_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not output.is_relative_to(runtime):
        raise ValueError('Rendered QA output must remain under the runtime root')
    dist = Path(__file__).resolve().parents[1] / 'dist'
    if not (dist / 'index.html').is_file():
        raise ValueError('Build the current frontend through scripts/run_frontend.py first')
    output.mkdir(parents=True, exist_ok=True)
    ensure_playwright()
    from playwright.sync_api import sync_playwright
    candidate = 'strategy-configuration:00000000-0000-0000-0000-000000000001'
    plan = dict(run_plan_id='ui-qa-programme', name='Programme UI review fixture',
        strategy_id='ui-qa-programme', strategy_revision=1, profile_id=candidate)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for theme, scale, viewport in product(('light', 'dark'), (.8, 1., 1.25),
                    ({'width': 1920, 'height': 1080}, {'width': 1280, 'height': 800})):
                context = browser.new_context(viewport=viewport)
                launches, preflights, errors = [], [], []
                context.add_init_script("localStorage.setItem('quant-research-workbench.theme', "
                    + json.dumps(theme) + "); localStorage.setItem('quant-research-workbench.ui-scale', "
                    + json.dumps(str(scale)) + ');')
                def route_request(route):
                    request = route.request
                    url = urlsplit(request.url)
                    if url.hostname != 'programme-review.invalid':
                        route.abort()
                        return
                    path = url.path
                    def response(value, status=200):
                        route.fulfill(status=status, content_type='application/json', body=json.dumps(value))
                    if path == '/api/trading/backtest/configuration-options':
                        response(dict(candidate_id=candidate, run_plan_id=plan['run_plan_id'],
                            candidates=[dict(candidate_id=candidate, candidate_revision=1,
                                label=plan['name'], content_hash='a' * 64)],
                            available_run_plans=[plan], error=''))
                    elif path == '/api/trading/historical-preflight':
                        payload = request.post_data_json
                        preflights.append(payload)
                        if payload.get('broker_liquidity_resolution_ms') == 100:
                            response(dict(detail='Requested broker liquidity differs from the prepared filling contract'), 400)
                            return
                        day = (date.fromisoformat(payload['anchor_date']) - timedelta(days=1)).isoformat()
                        response(dict(schema_version=1, mode='backtest', ready=True, strategy_run_ready=True,
                            initial_cash=payload['initial_cash'], configuration_revision_id=candidate,
                            configuration_revision=1, configuration_content_hash='a' * 64,
                            run_plan_id=plan['run_plan_id'], available_run_plans=[plan], automatic_strategy_count=1,
                            broker_liquidity_resolution_ms=1000,
                            window=dict(sessions=[day], start=day, end=day, session_count=1),
                            checks=[dict(id='prepared_programme_funnel', label='Prepared programme funnel',
                                status='ready', required=True, summary='Synthetic UI fixture only', evidence='ui-qa')]))
                    elif path == '/api/trading/backtest/runs':
                        if request.method == 'POST':
                            launches.append(request.post_data_json)
                            response(dict(detail='Launch intercepted by offline UI review; no run started'), 409)
                        else:
                            response(dict(rows=[]))
                    elif path.startswith('/api/'):
                        response(dict(detail='Offline programme UI fixture has no service for this endpoint'), 503)
                    else:
                        selected = (dist / path.lstrip('/')).resolve() if path != '/' else dist / 'index.html'
                        if not selected.is_relative_to(dist.resolve()) or not selected.is_file():
                            route.fulfill(status=404, body='Offline asset not found')
                        else:
                            route.fulfill(body=selected.read_bytes(), content_type=mimetypes.guess_type(selected.name)[0] or 'application/octet-stream')
                context.route('**/*', route_request)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto('http://programme-review.invalid/#backtest-trading', wait_until='domcontentloaded')
                    page.get_by_role('button', name='Broker liquidity clock', exact=True).wait_for(timeout=15000)
                    launch = page.get_by_role('button', name='Run Full-market Backtest', exact=True)
                    launch.wait_for()
                    page.wait_for_function("[...document.querySelectorAll('button')].some(b => b.textContent.includes('Run Full-market Backtest') && !b.disabled)")
                    page.get_by_role('button', name='Ticker scope', exact=True).click()
                    assert page.get_by_role('option', name='Selected tickers · shared portfolio', exact=True).count() == 0
                    page.keyboard.press('Escape')
                    page.get_by_role('button', name='Broker liquidity clock', exact=True).click()
                    page.get_by_role('option', name='100 milliseconds', exact=True).click()
                    page.get_by_text('Requested broker liquidity differs from the prepared filling contract', exact=False).first.wait_for()
                    assert launch.is_disabled()
                    page.get_by_role('button', name='Broker liquidity clock', exact=True).click()
                    page.get_by_role('option', name='1 second', exact=True).click()
                    page.wait_for_function("[...document.querySelectorAll('button')].some(b => b.textContent.includes('Run Full-market Backtest') && !b.disabled)")
                    name = f"programme-{theme}-{scale}-{viewport['width']}"
                    page.screenshot(path=str(output / (name + '.png')), full_page=True)
                    assert '100 ms evaluation interval' not in page.locator('#main-content').inner_text()
                    page.evaluate("document.querySelectorAll('*').forEach(element => { element.scrollTop = 0; })")
                    page.screenshot(path=str(output / (name + '-top.png')), full_page=True)
                    geometry = page.evaluate('({width:innerWidth, documentWidth:document.documentElement.scrollWidth})')
                    assert geometry['documentWidth'] <= geometry['width'] + 1, geometry
                    launch.click()
                    page.get_by_text('Launch intercepted by offline UI review; no run started', exact=False).first.wait_for()
                    assert len(launches) == 1 and launches[0]['tickers'] == []
                    assert launches[0]['experimental_structure_book'] == ''
                    assert launches[0]['broker_liquidity_resolution_ms'] == 1000
                    assert preflights and not errors, errors
                    results.append(dict(theme=theme, scale=scale, viewport=viewport,
                        screenshot=name + '.png', geometry=geometry, intercepted_launch=launches[0]))
                    print(name + ': passed', flush=True)
                finally:
                    context.close()
        finally:
            browser.close()
    (output / 'review.json').write_text(json.dumps(dict(contract='offline-programme-ui-review-v1',
        real_services_started=0, real_backtests_started=0, scenarios=results), indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
