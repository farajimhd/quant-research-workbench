"""Real canonical bars and HTTP V7 feed inside the production ChartPreview."""
import os
from datetime import datetime,timezone
from pathlib import Path
import pytest


@pytest.mark.skipif(not os.environ.get('CHART_BROWSER_TEST_URL'),reason='Managed frontend required')
@pytest.mark.parametrize('ticker,clock,width,height,scale,theme',[
    ('JUNS','07:18:40',1280,720,'1.25','dark'),('SUGP','04:15:20',1600,1000,'0.8','light')])
def test_v7_chart_completed_clock_and_rewind(ticker,clock,width,height,scale,theme):
    from playwright.sync_api import sync_playwright
    from src.market_engine.level_book_store import ROOT,read
    from src.market_engine.v7_qmd import QmdSource
    from src.backend.swing_book_source import session_bounds
    start,end=session_bounds('2026-08-21')
    sequence,_=QmdSource().seconds(ticker,'2026-08-21',start.timestamp(),end.timestamp(),'history')
    inputs={'bars':sequence}
    stamp=int(datetime.fromisoformat('2026-08-21T'+clock+'-04:00').timestamp())
    bars=[dict(bar_start=datetime.fromtimestamp(b['t']-1,timezone.utc).isoformat(),bar_end=datetime.fromtimestamp(b['t'],timezone.utc).isoformat(),session_date='2026-08-21',is_closed=True,**{k:b[k] for k in ('open','high','low','close','volume')}) for b in inputs['bars'] if stamp-240<b['t']<=stamp+20]
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':width,'height':height});responses=[]
            page.add_init_script("localStorage.setItem('quant-research-workbench.theme',"+repr(theme)+");localStorage.setItem('quant-research-workbench.ui-scale',"+repr(scale)+");")
            def receive(response):
                if '/api/research/level-book-v7/book' in response.url and response.status==200:responses.append(response.json())
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
            page.locator('.reaction-book-settings[data-book-status=ready]').wait_for(timeout=90000)
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
            page.locator('.reaction-book-settings summary').click()
            assert page.get_by_role('combobox',name='Level book source').count()==0
            assert page.get_by_label('Historical resistance color').count()==1
            assert page.get_by_label('Current-day support color').count()==1
            page.screenshot(path=str(output/f'{ticker}-v7-settings.png'))
            page.locator('.reaction-book-settings summary').click()
            with page.expect_response(lambda r:'/level-book-v7/book' in r.url and r.status==200):page.evaluate('t=>window.v7Paint(t)',stamp+10)
            with page.expect_response(lambda r:'/level-book-v7/book' in r.url and r.status==200):page.evaluate('t=>window.v7Paint(t)',stamp)
            assert responses[-1]==first
            assert all(s['valid_to']<=stamp for s in responses[-1]['segments'])
            page.evaluate('window.v7Root.unmount()')
        finally:browser.close()
