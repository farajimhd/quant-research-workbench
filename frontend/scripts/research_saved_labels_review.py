"""Browser acceptance of the real published V6 teacher dataset (no fixtures)."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:5173')
    parser.add_argument('--api-url',default='http://127.0.0.1:8000')
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    output=Path(args.output_dir).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()): raise ValueError('External runtime required')
    output.mkdir(parents=True,exist_ok=True)
    records=[]; errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            for theme in ['light','dark']:
                for scale in [.8,1.,1.25]:
                    for size,viewport in [('normal',dict(width=1600,height=1000)),('compact',dict(width=1280,height=720))]:
                        context=browser.new_context(viewport=viewport)
                        context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
                        page=context.new_page(); page.on('pageerror',lambda e:errors.append(str(e)))
                        responses=[]; requests=[]
                        def proxy(route):
                            parsed=urlsplit(route.request.url)
                            response=route.fetch(url=args.api_url+parsed.path+('?' + parsed.query if parsed.query else ''),timeout=300000)
                            if '/saved-labels/chart' in parsed.path:
                                if not response.ok:
                                    route.fulfill(response=response)
                                    return
                                raw=response.json(); data=raw['data'] if response.headers.get('x-response-envelope')=='1' else raw; responses.append(data)
                                assert len(data['candles'])==len(data['labels'])
                                assert all(c['endTime']==r['time_us']/1e6 and c['time']==c['endTime']-1 for c,r in zip(data['candles'],data['labels']))
                                assert data['quality_threshold']==.9
                                assert len(data['oscillator_series'])==3
                                assert data['timing']['feature_cutoff'].endswith('exclude target candle')
                                assert all(r['label_value'] is None or 0<=r['label_value']<=1 for r in data['labels'])
                            route.fulfill(response=response)
                        page.route('**/api/research/models**',proxy)
                        page.on('request',lambda r:requests.append(r.url) if '/saved-labels' in r.url else None)
                        page.goto(args.base_url+'/#research-workspace')
                        page.get_by_role('button',name='Current V6 labels',exact=True).click()
                        scope=page.locator('.research-path-content:visible')
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        assert 'NVDA' in scope.inner_text()
                        assert scope.get_by_label('Saved label session',exact=True).locator('option').count()==19
                        assert scope.get_by_label('Opportunity quality threshold',exact=True).count()==0
                        assert scope.get_by_label('Opportunity label view',exact=True).input_value()=='combined'
                        assert any(row['action']=='EXIT' for row in responses[-1]['labels'])
                        assert 'close t, never open' in scope.inner_text()
                        name=f'{theme}-{scale}-{size}'
                        page.screenshot(path=str(output/(name+'-workspace.png')))
                        scope.get_by_role('button',name='Fullscreen Price-action candles & labels',exact=True).click()
                        page.wait_for_timeout(200)
                        page.screenshot(path=str(output/(name+'-chart.png')))
                        scope.get_by_role('button',name='Exit fullscreen Price-action candles & labels',exact=True).click()
                        assert not page.evaluate('document.documentElement.scrollWidth>window.innerWidth+2')
                        if theme=='light' and scale==1 and size=='normal':
                            node=scope.locator('.research-price-action-chart canvas').first.element_handle()
                            count=len(requests)
                            page.get_by_role('link',name='Labeler',exact=True).click()
                            assert node.evaluate('(el)=>el.isConnected')
                            page.get_by_role('link',name='Research',exact=True).click()
                            scope.locator('.chart-shell').wait_for()
                            assert node.evaluate('(el)=>el===document.querySelector(".research-path-content:not([hidden]) .research-price-action-chart canvas")')
                            assert len(requests)==count
                            toolbar=scope.get_by_role('toolbar',name='Move Price-action algorithm. Use arrow keys to reposition; hold Shift for larger steps.',exact=True)
                            toolbar.focus(); toolbar.press('ArrowRight'); toolbar.press('ArrowDown')
                            resize=scope.get_by_role('button',name='Resize Price-action algorithm. Use arrow keys to resize; hold Shift for larger steps.',exact=True)
                            resize.focus(); resize.press('ArrowRight')
                            page.wait_for_timeout(100)
                            geometry=scope.locator('[data-window-kind="architecture"]').get_attribute('style')
                            page.get_by_role('link',name='Labeler',exact=True).click(); page.get_by_role('link',name='Research',exact=True).click()
                            assert scope.locator('[data-window-kind="architecture"]').get_attribute('style')==geometry
                            for view in ['held','reference','flat']:
                                scope.get_by_label('Opportunity label view',exact=True).select_option(view)
                                scope.locator('.chart-shell').wait_for(timeout=300000)
                                page.wait_for_timeout(100)
                            scope.get_by_label('Find saved label ticker',exact=True).fill('AAPL')
                            options=scope.get_by_label('Saved label listing',exact=True).locator('option').all_text_contents()
                            assert any('AAPL' in o for o in options)
                            option=scope.get_by_label('Saved label listing',exact=True).locator('option').filter(has_text='AAPL').first
                            scope.get_by_label('Saved label listing',exact=True).select_option(option.get_attribute('value'))
                            scope.locator('.chart-shell').wait_for(timeout=300000)
                            scope.get_by_label('Saved label session',exact=True).select_option('2026-08-25')
                            scope.locator('.chart-shell').wait_for(timeout=300000)
                            page.wait_for_timeout(200)
                            assert any('day=2026-08-25' in r for r in requests)
                        records.append(dict(theme=theme,scale=scale,viewport=size,charts=len(responses),dataset_sha256=responses[0]['dataset_sha256']))
                        context.close()
        finally: browser.close()
    assert not errors,errors
    (output/'report.json').write_text(json.dumps(dict(status='passed',cases=records,page_errors=errors),indent=2),encoding='utf-8')
    print(json.dumps(dict(status='passed',cases=len(records),output=str(output))))


if __name__=='__main__': main()
