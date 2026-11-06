"""Real published V66 label review through the application's Research route."""
import argparse
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--base-url',default='http://127.0.0.1:5199')
    p.add_argument('--output-dir',required=True)
    a=p.parse_args();output=Path(a.output_dir).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime output required')
    output.mkdir(parents=True,exist_ok=True);records=[];errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        try:
            for theme in ('light','dark'):
                for scale in (.8,1.,1.25):
                    for size,viewport in [('normal',dict(width=1600,height=1000)),('compact',dict(width=1280,height=720))]:
                        context=browser.new_context(viewport=viewport)
                        context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
                        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        responses=[]
                        def receive(response):
                            if '/v66/labels/chart?' in response.url and response.ok:
                                data=response.json()
                                if response.headers.get('x-response-envelope')=='1':data=data['data']
                                assert len(data['candles'])==len(data['labels'])<=120
                                assert all(c['endTime']==r['time_us']/1e6 for c,r in zip(data['candles'],data['labels']))
                                responses.append(data)
                        page.on('response',receive)
                        page.goto(a.base_url+'/?research=v66-labels#research-workspace')
                        scope=page.locator('.research-path-content:not([hidden])')
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        assert scope.get_by_role('button',name='V7 ranked slots').count()==0
                        name=f'{theme}-{scale}-{size}'
                        page.screenshot(path=str(output/(name+'-1a.png')))
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'phase=1b' in r.url):
                            scope.get_by_label('V66 label phase',exact=True).select_option('1b')
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        timeline=scope.get_by_role('region',name='Episode grouping chart')
                        band=timeline.get_by_role('button',name=re.compile('^Inspect group ')).first
                        band.wait_for(timeout=300000)
                        timeline.get_by_label('Grouping chart scale',exact=True).select_option('log')
                        group_id=band.get_attribute('aria-label').split()[-1]
                        with page.expect_response(lambda r:'/v66/labels/market?' in r.url and 'group_id='+group_id in r.url):
                            band.click()
                        expect(scope.get_by_label('V66 overlap group',exact=True)).to_have_value(group_id)
                        scope.get_by_label('V66 overlap group',exact=True).select_option('')
                        scope.get_by_text('1b selection, overlap groups & relative sizing',exact=True).click()
                        box=timeline.locator('[role=button][data-selected=true]').first
                        selected_pair=int(box.get_attribute('aria-label').split()[-1])
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'start_us=' in r.url and 'phase=1a' in r.url):
                            box.click()
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        expect(scope.get_by_label('V66 label phase',exact=True)).to_have_value('1b')
                        expect(scope.get_by_text('1a · V66 saved labels',exact=True)).to_be_visible()
                        assert any(r['pair_id']==selected_pair for r in responses[-1]['labels'])
                        assert all(r['action']==r['action_1a'] for r in responses[-1]['labels'])
                        timeline.screenshot(path=str(output/(name+'-timeline.png')))
                        scope.get_by_text('1b selection, overlap groups & relative sizing',exact=True).click()
                        scope.get_by_label('V66 pair selection',exact=True).select_option('selected')
                        page.wait_for_function("document.querySelectorAll('.research-v66-decisions tbody tr').length > 0")
                        first=scope.locator('.research-v66-decisions tbody button').first
                        pair=int(first.inner_text().split('#')[-1])
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'start_us=' in r.url):
                            first.click()
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        assert any(r['pair_id']==pair for r in responses[-1]['labels'])
                        page.screenshot(path=str(output/(name+'-1b.png')))
                        scope.get_by_text('1b selection, overlap groups & relative sizing',exact=True).click()
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'view=held' in r.url):
                            scope.get_by_label('Opportunity label view',exact=True).select_option('held')
                        scope.locator('.chart-shell').wait_for(timeout=300000)
                        scope.get_by_role('button',name='Fullscreen Price-action candles & labels',exact=True).click()
                        page.screenshot(path=str(output/(name+'-held-fullscreen.png')))
                        scope.get_by_role('button',name='Exit fullscreen Price-action candles & labels',exact=True).click()
                        if theme=='light' and scale==1 and size=='normal':
                            sessions=scope.get_by_label('V66 label session',exact=True).locator('option')
                            last=sessions.last.get_attribute('value')
                            with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'day='+last in r.url):
                                scope.get_by_label('V66 label session',exact=True).select_option(last)
                            scope.locator('.chart-shell').wait_for(timeout=300000)
                        assert responses
                        records.append(dict(theme=theme,scale=scale,viewport=size,charts=len(responses),dataset=responses[0]['dataset_sha256']))
                        context.close()
        finally:browser.close()
    if errors:raise AssertionError(errors)
    (output/'report.json').write_text(json.dumps(dict(status='passed',cases=records,page_errors=errors),indent=2))
    print(json.dumps(dict(status='passed',cases=len(records),output=str(output))))


if __name__=='__main__':main()
