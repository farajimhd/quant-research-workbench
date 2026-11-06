"""Exercise the real published V66 timeline-to-candles review workflow."""
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:5173')
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--matrix',choices=('targeted','exhaustive'),default='targeted')
    args=parser.parse_args();output=Path(args.output_dir).resolve()
    if not output.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime output required')
    output.mkdir(parents=True,exist_ok=True);records=[];errors=[]
    scales=(.8,.9,1.,1.1,1.25) if args.matrix=='exhaustive' else (.8,1.,1.25)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        try:
            for theme in ('light','dark'):
                for scale in scales:
                    for size,viewport in [('normal',dict(width=1600,height=1000)),('compact',dict(width=1280,height=720))]:
                        context=browser.new_context(viewport=viewport)
                        context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
                        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        charts=[];timelines=[]
                        def receive(response):
                            if '/v66/labels/' not in response.url or not response.ok:return
                            if '/chart?' not in response.url and '/timeline?' not in response.url:return
                            data=response.json()
                            if response.headers.get('x-response-envelope')=='1':data=data['data']
                            if '/chart?' in response.url:
                                assert len(data['candles'])==len(data['labels'])
                                assert all(c['endTime']==r['time_us']/1e6 for c,r in zip(data['candles'],data['labels']))
                                params=parse_qs(urlparse(response.url).query)
                                if 'segment_end_us' not in params:assert len(data['candles'])<=120
                                charts.append((params,data))
                            else:timelines.append(data)
                        page.on('response',receive)
                        page.goto(args.base_url+'/?research=v66-labels#research-workspace')
                        scope=page.locator('.research-v66-workspace')
                        chart=scope.locator('.research-stacked-chart')
                        chart.locator('.chart-shell').wait_for(timeout=60000)
                        name=f'{theme}-{scale}-{size}'
                        assert scope.locator('.research-stacked-detail[open]').count()==0
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'phase=1b' in r.url):
                            scope.get_by_label('V66 label phase',exact=True).select_option('1b')
                        # Inspect the actual 1b evidence before timeline navigation
                        # intentionally opens the preserved 1a comparison segment.
                        if charts[-1][1]['label_version']=='v66-hindsight-execution-spread-sidecar-v3':
                            data=charts[-1][1]
                            assert all(r['quote_source']=='execution_liquidity' for r in data['labels'])
                            assert all(r['action']=='UNLABELLED' for r in data['labels'] if not r['economic_label_valid'])
                            scope.get_by_text('Session & episode values',exact=True).click()
                            evidence=scope.locator('.research-candle-values')
                            expect(evidence.get_by_text('Certified execution liquidity · standing book',exact=True)).to_be_visible()
                            expect(evidence.get_by_text('Quote age · seconds',exact=True)).to_be_visible()
                            expect(evidence.get_by_text('Economic label evidence',exact=True)).to_be_visible()
                            evidence.screenshot(path=str(output/(name+'-evidence.png')))
                            scope.get_by_text('Session & episode values',exact=True).click()
                        timeline=scope.get_by_role('region',name='Episode grouping chart')
                        box=timeline.locator('[role=button][data-selected=true]').first
                        box.wait_for(timeout=60000)
                        assert timeline.bounding_box()['y']<chart.bounding_box()['y']
                        timeline.get_by_label('Grouping chart scale',exact=True).select_option('log')
                        pair_id=int(box.get_attribute('aria-label').split()[-1])
                        ticker=box.get_attribute('aria-label').split()[1]
                        segment=next(r for r in timelines[-1]['rows'] if r['ticker']==ticker and r['pair_id']==pair_id)
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'segment_end_us=' in r.url and 'phase=1a' in r.url):
                            box.locator('rect').click(force=True)
                        boundaries=chart.locator('[data-reference-boundary]')
                        expect(boundaries).to_have_count(2,timeout=60000)
                        expect(scope.get_by_label('V66 label phase',exact=True)).to_have_value('1b')
                        expect(chart.locator('.research-toolbar strong')).to_have_text(ticker+' · Price-action candles & labels')
                        data=charts[-1][1];clocks=[r['time_us'] for r in data['labels']]
                        first=clocks.index(segment['time_us']);last=clocks.index(segment['entry_target_us'])
                        assert first<=30 and len(clocks)-last-1<=30
                        assert any(r['pair_id']==pair_id for r in data['labels'])
                        assert all(r['action']==r['action_1a'] for r in data['labels'])
                        position=chart.locator(f'.chart-position-price-line[data-position-pair="{pair_id}"]')
                        expect(position).to_have_count(2)
                        entry_price=data['candles'][first]['close'];exit_price=data['candles'][last]['close']
                        assert float(chart.locator(f'.chart-position-price-line.entry[data-position-pair="{pair_id}"]').get_attribute('data-position-price'))==entry_price
                        assert float(chart.locator(f'.chart-position-price-line.exit[data-position-pair="{pair_id}"]').get_attribute('data-position-price'))==exit_price
                        difference=exit_price-entry_price
                        expect(position.locator('span').filter(has_text='Δ')).to_have_text(f'#{pair_id} Exit ${exit_price:.4f} · Δ +${difference:.4f}/share')
                        page.wait_for_function("""() => {
                            const lines=[...document.querySelectorAll('.research-stacked-chart [data-reference-boundary]')];
                            return lines.length===2 && lines.every(e=>getComputedStyle(e).borderLeftStyle==='dashed' && e.offsetLeft>=0 && e.offsetLeft<=e.parentElement.clientWidth);
                        }""")
                        geometry=chart.locator('.chart-shell').bounding_box();workspace=scope.bounding_box()
                        assert geometry['width']>=workspace['width']-40*scale
                        assert geometry['height']>=400*scale
                        assert scope.locator('.research-v66-market').bounding_box()['y']>geometry['y']+geometry['height']
                        chart.scroll_into_view_if_needed()
                        page.screenshot(path=str(output/(name+'-segment.png')))
                        if data['next_available']:
                            with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'segment_end_us=' not in r.url):
                                chart.get_by_role('button',name='Next 120 candles',exact=True).click()
                            chart.locator('.chart-shell').wait_for()
                            box.press('Enter')
                            expect(boundaries).to_have_count(2)
                        with page.expect_response(lambda r:'/v66/labels/chart?' in r.url and 'view=held' in r.url):
                            chart.get_by_label('Opportunity label view',exact=True).select_option('held')
                        expect(boundaries).to_have_count(2)
                        chart.get_by_role('button',name='Fullscreen',exact=True).click()
                        expect(chart.locator('.chart-shell')).to_have_class('chart-shell fullscreen fill-height')
                        page.screenshot(path=str(output/(name+'-fullscreen.png')))
                        chart.get_by_role('button',name='Exit fullscreen',exact=True).click()
                        scope.get_by_text('Session & episode values',exact=True).click()
                        expect(scope.get_by_label('Price-action episode pair',exact=True)).to_have_value(str(pair_id))
                        scope.get_by_text('Session & episode values',exact=True).click()
                        band=timeline.locator('[aria-label^="Inspect group "]').first
                        group_id=band.get_attribute('aria-label').split()[-1]
                        band.press('Enter')
                        expect(scope.get_by_label('V66 overlap group',exact=True)).to_have_value(group_id)
                        records.append(dict(theme=theme,scale=scale,viewport=size,ticker=ticker,pair_id=pair_id,candles=len(clocks),before=first,after=len(clocks)-last-1,geometry=geometry))
                        context.close()
        finally:browser.close()
    if errors:raise AssertionError(errors)
    (output/'report.json').write_text(json.dumps(dict(status='passed',cases=records,page_errors=errors),indent=2))
    print(json.dumps(dict(status='passed',cases=len(records),output=str(output))))


if __name__=='__main__':main()
