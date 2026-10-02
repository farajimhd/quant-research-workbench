"""Real saved price-action experiment review through the managed frontend."""
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
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Review output must remain under runtime root')
    output.mkdir(parents=True,exist_ok=True)
    records, errors, navigations = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for theme in ['light','dark']:
                for scale in [.8,1.,1.25]:
                    for size, viewport in [('normal',dict(width=1600,height=1000)),('compact',dict(width=1280,height=720))]:
                        context = browser.new_context(viewport=viewport)
                        context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
                        context.add_init_script("""window.researchTextRows=[];
                          const original=CanvasRenderingContext2D.prototype.fillText;
                          CanvasRenderingContext2D.prototype.fillText=function(text,x,y,...rest){
                            const width=this.measureText(String(text)).width;
                            const cx=x+(this.textAlign==='center'?0:this.textAlign==='right'||this.textAlign==='end'?-width/2:width/2);
                            window.researchTextRows.push({text:String(text),x:cx,y});
                            if(window.researchTextRows.length>50000)window.researchTextRows.splice(0,10000);
                            return original.call(this,text,x,y,...rest);
                          };""")
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        responses = []
                        def proxy(route):
                            parsed = urlsplit(route.request.url)
                            response = route.fetch(url=args.api_url+parsed.path+('?' + parsed.query if parsed.query else ''),timeout=300000)
                            assert response.ok, response.text()
                            data = response.json()
                            if '/price-action/chart' in parsed.path:
                                assert len(data['candles']) == len(data['labels'])
                                assert {'var(--success)','var(--danger)'} <= {r['color'] for r in data['regions']}
                                assert all(0<=r['label_value']<=1 for r in data['labels'])
                                if data['view']=='flat':
                                    assert set(r['action'] for r in data['labels'])<= {'ENTRY','WAIT'}
                                responses.append(data)
                            route.fulfill(response=response)
                        page.route('**/api/research/models**',proxy)
                        page.goto(args.base_url+'/#research-workspace')
                        page.get_by_role('button',name='Price-action experiment',exact=True).click()
                        scope = page.locator('.research-path-content:visible')
                        scope.locator('.research-price-action-chart .chart-shell').wait_for(timeout=300000)
                        page.wait_for_timeout(300)
                        assert scope.locator('.workspace-window').count() == 3
                        assert '22,748' in scope.inner_text()
                        assert '652' in scope.inner_text()
                        name = f'{theme}-{scale}-{size}'
                        page.screenshot(path=str(output/(name+'-workspace.png')))
                        page.get_by_role('button',name='Fullscreen Price-action candles & labels',exact=True).click()
                        page.locator('.research-path-content:visible .research-page').evaluate('(el)=>el.scrollTop=0')
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(output/(name+'-chart.png')))
                        painted = page.evaluate('window.researchTextRows')
                        for action, raw_field in [('ENTRY','entry_gain'),('EXIT','exit_gain')]:
                            candidates = [r for r in responses[0]['labels'] if r['action']==action]
                            assert any(any(a['text']==f"{r['label_value']:.3f}" and
                                           b['text']==f"{r[raw_field]:.4f}" and
                                           abs(a['x']-b['x'])<1 and b['y']>a['y']
                                           for a in painted for b in painted if b['text']==f"{r[raw_field]:.4f}")
                                       for r in candidates), f'{name}: {action} raw row not beneath quality'
                        page.get_by_role('button',name='Exit fullscreen Price-action candles & labels',exact=True).click()
                        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth+2')
                        if theme=='light' and scale==1 and size=='normal':
                            requests = []
                            page.on('request',lambda r: requests.append(r.url) if '/api/research/models' in r.url else None)
                            node = scope.locator('.research-price-action-chart canvas').first.element_handle()
                            for destination in ['teacher-path','sidebar']:
                                count = len(requests)
                                if destination=='teacher-path':
                                    page.get_by_role('button',name='V6 teacher labels',exact=True).click()
                                else:
                                    page.get_by_role('link',name='Labeler',exact=True).click()
                                page.locator('.research-price-action-chart').wait_for(state='hidden')
                                assert node.evaluate('(el)=>el.isConnected')
                                if destination=='teacher-path':
                                    page.get_by_role('button',name='Price-action experiment',exact=True).click()
                                else:
                                    page.get_by_role('link',name='Research',exact=True).click()
                                page.locator('.research-price-action-chart .chart-shell').wait_for()
                                page.wait_for_timeout(300)
                                assert node.evaluate('(el)=>el === document.querySelector(".research-price-action-chart canvas")')
                                assert len(requests)==count
                                navigations.append(dict(destination=destination,same_canvas=True,new_requests=0))
                            page.get_by_label('Price-action candle',exact=True).select_option(index=10)
                            assert page.locator('.research-candle-values').is_visible()
                            page.get_by_label('Show HOLD values',exact=True).check()
                            page.get_by_label('Opportunity quality threshold',exact=True).select_option('0.95')
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_label('Opportunity label view',exact=True).select_option('reference')
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_label('Opportunity label view',exact=True).select_option('flat')
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_label('Opportunity label view',exact=True).select_option('held')
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_label('Opportunity label view',exact=True).select_option('combined')
                            page.get_by_label('Opportunity quality threshold',exact=True).select_option('0.9')
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_label('Price-action episode pair',exact=True).select_option(index=200)
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            assert page.get_by_label('Price-action episode pair',exact=True).input_value()=='201'
                            page.get_by_role('button',name='Next 15 min',exact=True).click()
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            page.get_by_role('button',name='Previous 15 min',exact=True).click()
                            page.locator('.research-price-action-chart .chart-shell').wait_for()
                            toolbar = page.get_by_role('toolbar',name='Move Price-action algorithm. Use arrow keys to reposition; hold Shift for larger steps.',exact=True)
                            toolbar.focus(); toolbar.press('ArrowRight'); toolbar.press('ArrowDown')
                            resize = page.get_by_role('button',name='Resize Price-action algorithm. Use arrow keys to resize; hold Shift for larger steps.',exact=True)
                            resize.focus(); resize.press('ArrowRight')
                            page.wait_for_timeout(100)
                            geometry = page.locator('.research-path-content:visible [data-window-kind="architecture"]').evaluate('(el)=>el.getAttribute("style")')
                            page.get_by_role('link',name='Labeler',exact=True).click()
                            page.get_by_role('link',name='Research',exact=True).click()
                            assert page.locator('.research-path-content:visible [data-window-kind="architecture"]').evaluate('(el)=>el.getAttribute("style")')==geometry
                            page.reload()
                            page.get_by_role('button',name='Price-action experiment',exact=True).click()
                            page.locator('.research-price-action-chart .chart-shell').wait_for(timeout=300000)
                            assert page.locator('.research-path-content:visible [data-window-kind="architecture"]').evaluate('(el)=>el.getAttribute("style")')==geometry
                        records.append(dict(theme=theme,scale=scale,viewport=size,chart_windows=len(responses)))
                        context.close()
        finally:
            browser.close()
    report = dict(scenarios=records,page_errors=errors,navigation=navigations,source='real NVDA price-action artifacts',api_url=args.api_url)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    if errors: raise RuntimeError('Browser errors detected')


if __name__=='__main__':
    main()
