"""Real canonical bars and HTTP V7 feed inside the production ChartPreview."""
import os
from datetime import datetime,timezone
from pathlib import Path
import pytest


@pytest.mark.skipif(not os.environ.get('CHART_BROWSER_TEST_URL'),reason='Managed frontend required')
@pytest.mark.parametrize('ticker,clock,width,height,scale,theme',[
    ('JUNS','07:18:40',1280,720,'1.25','dark'),('SUGP','04:15:20',1600,1000,'0.8','light'),('SUGP','04:15:20',1440,900,'1','light')])
def test_v7_chart_completed_clock_and_rewind(ticker,clock,width,height,scale,theme):
    from playwright.sync_api import sync_playwright
    from src.market_engine.level_book_store import ROOT,read
    from src.market_engine.v7_qmd import QmdSource
    from src.backend.swing_book_source import session_bounds
    start,end=session_bounds('2026-08-21')
    sequence,_=QmdSource().seconds(ticker,'2026-08-21',start.timestamp(),end.timestamp(),'history')
    inputs={'bars':sequence}
    prior_bars=[]
    if scale=='1':
        previous_start,previous_end=session_bounds('2026-08-20')
        prior_bars,_=QmdSource().seconds(ticker,'2026-08-20',previous_start.timestamp(),previous_end.timestamp(),'history')
        assert prior_bars
        prior_bars=prior_bars[-120:]
    stamp=int(datetime.fromisoformat('2026-08-21T'+clock+'-04:00').timestamp())
    bars=[dict(bar_start=datetime.fromtimestamp(b['t']-1,timezone.utc).isoformat(),bar_end=datetime.fromtimestamp(b['t'],timezone.utc).isoformat(),session_date='2026-08-21',is_closed=True,**{k:b[k] for k in ('open','high','low','close','volume')}) for b in inputs['bars'] if stamp-240<b['t']<=stamp+20]
    bars=[dict(bar_start=datetime.fromtimestamp(b['t']-1,timezone.utc).isoformat(),bar_end=datetime.fromtimestamp(b['t'],timezone.utc).isoformat(),session_date='2026-08-20',is_closed=True,**{k:b[k] for k in ('open','high','low','close','volume')}) for b in prior_bars]+bars
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':width,'height':height});responses=[];checkpoints=[]
            page.add_init_script("localStorage.setItem('quant-research-workbench.theme',"+repr(theme)+");localStorage.setItem('quant-research-workbench.ui-scale',"+repr(scale)+");")
            def receive(response):
                if '/api/research/level-book-v7/book' in response.url and response.status==200:responses.append(response.json())
                if '/api/research/level-book-v7/chart-checkpoint' in response.url and response.status==200:checkpoints.append(response.json())
            page.on('response',receive);page.goto(os.environ['CHART_BROWSER_TEST_URL'])
            page.evaluate("""async ({bars,stamp,ticker})=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {ChartPreview}=await import('/src/features/canvas/chartPresentation.tsx');
              document.getElementById('root').style.display='none';const node=document.createElement('div');node.style.height='calc(100vh - 20px)';document.body.appendChild(node);
              const root=(dom.default??dom).createRoot(node);window.v7Root=root;
              window.v7Paint=(at)=>root.render(React.createElement(ChartPreview,{
                canvasId:'v7-test',instanceId:'v7-test',changeAsOf:new Date(at*1000).toISOString(),
                chartSettings:{symbol:ticker,timeframe:'1s',visibleIndicators:['indicator.qmd_unified_structure'],showVolume:true,showSplitEvents:false},linkContext:{symbol:ticker},symbolEditable:false,showTradeAnnotations:false,baseHeight:Math.min(700,innerHeight-170),
                onChartSettingsChange:()=>{},onLinkContextChange:()=>{},
                trading:{as_of:'2026-09-12T20:00:00+00:00',positions:[],orders:[],executions:[],strategy_chart_activity:[]},
                liveChart:{bars:bars.filter(b=>Date.parse(b.bar_end)/1000<=at),canLoadEarlier:false,connected:false,marketSignalEvents:[],error:'',historyError:'',historyNotice:'',indicators:[],indicatorsAvailable:true,structureEvents:[],structureLevelHistory:[],lastUpdateAt:'',loadEarlier:()=>{},loading:false,loadingEarlier:false,pointInTime:true,ready:true,splitAdjusted:false}
              }));window.v7Paint(stamp);
            }""",dict(bars=bars,stamp=stamp,ticker=ticker))
            page.get_by_role('button',name='Expand legend',exact=True).first.click()
            page.locator('.chart-legend-row[data-book-status=ready]').wait_for(timeout=90000)
            assert page.locator('.chart-toolbar').get_by_text('V7 settings',exact=True).count()==0
            first=responses[-1];assert first['as_of']==stamp
            assert first['book_version']=='causal-level-book-v7-mle-1'
            assert first['historical_count']>0 and first['merged_proposals']>0
            # Both windows reinforce historical levels; they must not be relabeled
            # as current-day levels merely to demonstrate streaming activity.
            fits={}
            for s in first['segments']:
                if s['historical']:fits.setdefault(s['id'],set()).add((s['lower'],s['upper'],s['fit']['count']))
            assert any(len(versions)>1 for versions in fits.values()),'Historical MLE fits did not update'
            visible=[s for s in first['segments'] if min(b['low'] for b in bars)<=s['price']<=max(b['high'] for b in bars) and s['valid_to']>=stamp-240]
            assert visible,'No V7 levels in chart viewport'
            output=ROOT/'browser-prefix';output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/f'{ticker}-v7.png'))
            page.get_by_role('button',name='Configure Level book V7',exact=True).click()
            assert page.get_by_role('combobox',name='Level book source').count()==0
            assert page.get_by_label('Historical resistance color').count()==1
            assert page.get_by_label('Current-day support color').count()==1
            dialog=page.get_by_role('dialog',name='Level book V7 presentation settings')
            assert dialog.is_visible()
            assert 'Public Sans' in dialog.evaluate('(e)=>getComputedStyle(e).fontFamily')
            assert float(dialog.evaluate('(e)=>getComputedStyle(e).zoom'))==float(scale)
            assert dialog.get_by_label('Role transitions color',exact=True).count()==1
            dialog.get_by_label('Show current-day support',exact=True).uncheck()
            assert not dialog.get_by_label('Show current-day support',exact=True).is_checked()
            dialog.get_by_label('Role transitions color',exact=True).fill('#123456')
            assert dialog.get_by_label('Role transitions color',exact=True).input_value()=='#123456'
            dialog.get_by_label('Show center lines',exact=True).uncheck()
            dialog.get_by_label('Show center lines',exact=True).check()
            assert dialog.get_by_role('table').count()==0
            assert all('Public Sans' in family for family in dialog.locator('output').evaluate_all('(nodes)=>nodes.map(e=>getComputedStyle(e).fontFamily)'))
            dialog.get_by_role('slider',name='Minimum reactions',exact=True).fill('5')
            dialog.get_by_role('slider',name='Maximum band width (bps)',exact=True).fill('50')
            assert dialog.get_by_role('slider',name='Minimum reactions',exact=True).input_value()=='5'
            assert dialog.get_by_role('slider',name='Maximum band width (bps)',exact=True).input_value()=='50'
            dialog.get_by_text('Streaming discovery',exact=True).click()
            page.wait_for_function("() => {const r=document.querySelector('.v7-legend-editor').getBoundingClientRect();return r.bottom<=innerHeight && r.right<=innerWidth;}")
            bounds=dialog.bounding_box();assert bounds and bounds['x']>=0 and bounds['y']>=0 and bounds['x']+bounds['width']<=width and bounds['y']+bounds['height']<=height
            page.screenshot(path=str(output/f'{ticker}-v7-settings.png'))
            page.screenshot(path=str(output/f'{ticker}-v7-legend-{scale}.png'))
            page.keyboard.press('Escape')
            assert not dialog.is_visible()
            page.get_by_role('button',name='Hide Level book V7',exact=True).click()
            assert page.get_by_role('button',name='Show Level book V7',exact=True).is_visible()
            page.get_by_role('button',name='Show Level book V7',exact=True).click()
            page.get_by_role('button',name='Configure Level book V7',exact=True).click()
            assert not dialog.get_by_label('Show current-day support',exact=True).is_checked()
            assert dialog.get_by_role('slider',name='Minimum reactions',exact=True).input_value()=='5'
            assert dialog.get_by_role('slider',name='Maximum band width (bps)',exact=True).input_value()=='50'
            assert dialog.get_by_label('Role transitions color',exact=True).input_value()=='#123456'
            dialog.get_by_role('button',name='Reset',exact=True).click()
            assert dialog.get_by_label('Show current-day support',exact=True).is_checked()
            assert dialog.get_by_role('slider',name='Minimum reactions',exact=True).input_value()=='0'
            assert dialog.get_by_role('slider',name='Maximum band width (bps)',exact=True).input_value()=='0'
            page.keyboard.press('Escape')
            with page.expect_response(lambda r:'/level-book-v7/book' in r.url and r.status==200):page.evaluate('t=>window.v7Paint(t)',stamp+10)
            with page.expect_response(lambda r:'/level-book-v7/book' in r.url and r.status==200):page.evaluate('t=>window.v7Paint(t)',stamp)
            assert responses[-1]==first
            assert all(s['valid_to']<=stamp for s in responses[-1]['segments'])
            if prior_bars:
                rect=page.locator('.chart-pane-canvas').first.bounding_box();assert rect
                for _ in range(25):
                    if checkpoints:break
                    page.mouse.move(rect['x']+rect['width']*.2,rect['y']+rect['height']*.6)
                    page.mouse.down();page.mouse.move(rect['x']+rect['width']*.8,rect['y']+rect['height']*.6,steps=15);page.mouse.up()
                    page.wait_for_timeout(500)
                page.locator('.chart-legend-row[data-prior-book-status=ready]').wait_for(timeout=30000)
                assert any(d['session_date']=='2026-08-20' and d['segments'] for d in checkpoints)
                assert all(s['valid_to']<=d['available_at'] and not s['model_input'] for d in checkpoints for s in d['segments'])
                for _ in range(3):
                    page.mouse.move(rect['x']+rect['width']*.2,rect['y']+rect['height']*.6)
                    page.mouse.down();page.mouse.move(rect['x']+rect['width']*.8,rect['y']+rect['height']*.6,steps=15);page.mouse.up()
                page.locator('.chart-legend-row[data-prior-book-status=ready]').wait_for(timeout=30000)
                page.screenshot(path=str(output/'SUGP-v7-prior-day.png'))
            page.evaluate('window.v7Root.unmount()')
        finally:browser.close()
