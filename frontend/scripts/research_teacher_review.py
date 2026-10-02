"""Research-only browser acceptance against real teacher API, with screenshots.

An explicit --api-url can target the isolated read-only router while the active
app backend remains untouched. No fixture labels replace the source evidence.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5173')
    parser.add_argument('--api-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--skip-development-chart', action='store_true', help='Repeat the layout matrix after development chart acceptance has already passed')
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Review output must remain under runtime root')
    output.mkdir(parents=True, exist_ok=True)
    records, errors, navigation_checks = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for theme in ('light', 'dark'):
                for scale in (.8, 1., 1.25):
                    for size, viewport in [('normal', dict(width=1600, height=1000)), ('compact', dict(width=1280, height=720))]:
                        context = browser.new_context(viewport=viewport)
                        context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme', '{theme}'); localStorage.setItem('quant-research-workbench.ui-scale', '{scale}');")
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        def proxy(route):
                            parsed = urlsplit(route.request.url)
                            response = route.fetch(url=args.api_url + parsed.path + ('?' + parsed.query if parsed.query else ''), timeout=600000)
                            route.fulfill(response=response)
                        page.route('**/api/research/models**', proxy)
                        page.goto(args.base_url + '/#research-workspace')
                        page.get_by_role('button', name='Preflight labels').click()
                        page.get_by_role('button', name='Open teacher audit workspace').wait_for(timeout=300000)
                        name = f'{theme}-{scale}-{size}'
                        page.screenshot(path=str(output / (name + '-preflight.png')))
                        page.get_by_role('button', name='Open teacher audit workspace').click()
                        page.locator('.research-chart-container .chart-shell').wait_for(timeout=600000)
                        page.wait_for_timeout(300)
                        assert page.locator('.workspace-window').count() == 3
                        page.screenshot(path=str(output / (name + '-workspace.png')))
                        page.get_by_role('button', name='Fullscreen Candles & hindsight labels', exact=True).click()
                        page.locator('.research-page').evaluate('(element) => element.scrollTop = 0')
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(output / (name + '-chart.png')))
                        page.get_by_role('button', name='Exit fullscreen Candles & hindsight labels', exact=True).click()
                        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
                        assert not overflow, f'Horizontal document overflow: {name}'
                        if theme == 'light' and scale == 1. and size == 'normal':
                            move = page.get_by_role('toolbar', name='Move Model architecture & details. Use arrow keys to reposition; hold Shift for larger steps.', exact=True)
                            move.focus(); move.press('ArrowRight'); move.press('ArrowDown')
                            resize = page.get_by_role('button', name='Resize Model architecture & details. Use arrow keys to resize; hold Shift for larger steps.', exact=True)
                            resize.focus(); resize.press('ArrowRight'); resize.press('ArrowDown')
                            page.wait_for_timeout(100)
                            storage_key = 'research.v6.workspace.v1:2026-07-31:layouts'
                            geometry = page.evaluate('(key) => JSON.parse(localStorage.getItem(key)).architecture', storage_key)
                            assert geometry['x'] > 0 and geometry['y'] > 0
                            page.reload()
                            page.get_by_role('button', name='Preflight labels').click()
                            page.get_by_role('button', name='Open teacher audit workspace').wait_for(timeout=300000)
                            page.get_by_role('button', name='Open teacher audit workspace').click()
                            page.locator('.research-chart-container .chart-shell').wait_for(timeout=600000)
                            restored = page.locator('[data-window-kind="architecture"]').evaluate('(element) => ({ x: parseFloat(element.style.left), y: parseFloat(element.style.top), w: parseFloat(element.style.width), h: parseFloat(element.style.height) })')
                            assert all(abs(restored[k] - geometry[k]) < 1 for k in ('x', 'y', 'w', 'h'))
                            page.get_by_label('Label branch', exact=True).select_option('held')
                            page.locator('.research-chart-container .chart-shell').wait_for(timeout=600000)
                            research_requests = []
                            page.on('request', lambda request: research_requests.append(request.url) if '/api/research/models' in request.url else None)
                            chart_node = page.locator('.research-chart-container .chart-shell').element_handle()
                            chart_canvas = page.locator('.research-chart-container canvas').first.element_handle()
                            for destination in ('Public Sans Roles', 'Labeler'):
                                request_count = len(research_requests)
                                page.get_by_role('link', name=destination, exact=True).click()
                                page.locator('.research-chart-container').wait_for(state='hidden')
                                assert chart_node.evaluate('(element) => element.isConnected')
                                assert chart_canvas.evaluate('(element) => element.isConnected')
                                page.get_by_role('link', name='Research', exact=True).click()
                                page.locator('.research-chart-container .chart-shell').wait_for(timeout=10000)
                                page.wait_for_timeout(300)
                                assert page.get_by_label('Label branch', exact=True).input_value() == 'held'
                                assert page.get_by_role('button', name='Preflight labels').count() == 0
                                assert chart_node.evaluate('(element) => element === document.querySelector(".research-chart-container .chart-shell")')
                                assert chart_canvas.evaluate('(element) => element === document.querySelector(".research-chart-container canvas")')
                                assert len(research_requests) == request_count, research_requests[request_count:]
                                returned = page.locator('[data-window-kind="architecture"]').evaluate('(element) => ({ x: parseFloat(element.style.left), y: parseFloat(element.style.top), w: parseFloat(element.style.width), h: parseFloat(element.style.height) })')
                                assert all(abs(returned[k] - geometry[k]) < 1 for k in ('x', 'y', 'w', 'h'))
                                navigation_checks.append(dict(destination=destination, same_chart_nodes=True, research_requests=0, branch='held', geometry_preserved=True))
                            page.get_by_role('button', name='Fullscreen Candles & hindsight labels', exact=True).click()
                            page.wait_for_timeout(300)
                            page.screenshot(path=str(output / 'held-exit-chart.png'))
                            page.get_by_role('button', name='Exit fullscreen Candles & hindsight labels', exact=True).click()
                            page.get_by_text('How teacher labels are calculated', exact=True).click()
                            assert 'score >= 0.01' in page.locator('.research-label-detail').first.inner_text()
                            page.get_by_text('How teacher labels are calculated', exact=True).click()
                            page.get_by_label('Episode', exact=True).select_option(index=1)
                            page.locator('.research-chart-container .chart-shell').wait_for(timeout=600000)
                            page.get_by_role('button', name='Close Model architecture & details', exact=True).click()
                            assert page.locator('.workspace-window').count() == 2
                            page.get_by_role('button', name='Restore Model architecture & details', exact=True).click()
                            assert page.locator('.workspace-window').count() == 3
                            page.get_by_role('button', name='Reset containers', exact=True).click()
                            page.get_by_role('button', name='Models & preflight', exact=True).click()
                            page.get_by_label('Label session', exact=True).select_option('2026-08-24')
                            assert page.get_by_role('button', name='Open teacher audit workspace').count() == 0
                            page.get_by_role('button', name='Preflight labels').click()
                            page.get_by_role('button', name='Open teacher audit workspace').wait_for(timeout=300000)
                            if not args.skip_development_chart:
                                page.get_by_role('button', name='Open teacher audit workspace').click()
                                page.locator('.research-chart-container .chart-shell').wait_for(timeout=600000)
                                page.screenshot(path=str(output / 'development-workspace.png'))
                        records.append(dict(theme=theme, scale=scale, viewport=size, overflow=overflow))
                        context.close()
        finally:
            browser.close()
    report = dict(scenarios=records, page_errors=errors, navigation_checks=navigation_checks, api_url=args.api_url, source='real saved teacher labels', development_chart_checked=not args.skip_development_chart)
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    if errors:
        raise RuntimeError('Browser page errors detected')


if __name__ == '__main__':
    main()
