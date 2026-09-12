"""Real model + ChartPreview integration, including the replay/wall-clock split."""
import os
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get('CHART_BROWSER_TEST_URL'),reason='Managed frontend required')
def test_saved_debug_and_advancing_backtest_cursor():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from src.backend.level_reaction_service import router
    from research.reaction_levels.v1.inference import ROOT,read
    model='SUGP-2025-aug2026-v1-complete'
    inputs=read(ROOT/model/'inputs/2026-08-21.json')
    start=int(datetime.fromisoformat('2026-08-21T04:15:00-04:00').timestamp())
    bars=[dict(bar_start=datetime.fromtimestamp(b['t']-1,timezone.utc).isoformat(),
               bar_end=datetime.fromtimestamp(b['t'],timezone.utc).isoformat(),session_date='2026-08-21',is_closed=True,
               **{k:b[k] for k in ('open','high','low','close','volume')})
          for b in inputs['bars'] if start-120<b['t']<=start+60]
    app=FastAPI();app.include_router(router);client=TestClient(app)
    calls=[]
    def endpoint(route):
        body=route.request.post_data_json
        path=route.request.url.split('/api/',1)[1]
        if body:
            calls.append((path,body))
            response=client.post('/api/'+path,json=body)
        else:
            response=client.get('/api/'+path)
        route.fulfill(status=response.status_code,content_type='application/json',body=response.text)
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1600,'height':1000})
            page.route('**/api/research/level-reaction/**',endpoint)
            page.goto(os.environ['CHART_BROWSER_TEST_URL'])
            page.evaluate("""async ({bars,start})=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {ChartPreview}=await import('/src/features/canvas/chartPresentation.tsx');
              document.getElementById('root').style.display='none';
              const node=document.createElement('div');node.style.height='900px';document.body.appendChild(node);
              const root=(dom.default??dom).createRoot(node);window.reactionRoot=root;
              window.reactionPaint=(stamp,frame='1s')=>root.render(React.createElement(ChartPreview,{
                canvasId:'reaction-regression',instanceId:'reaction-regression',changeAsOf:new Date(stamp*1000).toISOString(),
                chartSettings:{symbol:'SUGP',timeframe:frame,visibleIndicators:[],showVolume:true,showSplitEvents:false},
                linkContext:{symbol:'SUGP'},symbolEditable:false,showTradeAnnotations:false,baseHeight:650,
                onChartSettingsChange:()=>{},onLinkContextChange:()=>{},
                trading:{as_of:'2026-09-12T19:21:01.765221+00:00',positions:[],orders:[],executions:[],strategy_chart_activity:[]},
                liveChart:{bars:bars.filter(b=>Date.parse(b.bar_end)/1000<=stamp),canLoadEarlier:false,connected:false,marketSignalEvents:[],
                  error:'',historyError:'',historyNotice:'',indicators:[],indicatorsAvailable:true,structureEvents:[],structureLevelHistory:[],
                  lastUpdateAt:'',loadEarlier:()=>{},loading:false,loadingEarlier:false,pointInTime:true,ready:true,splitAdjusted:false}
              }));
              window.reactionPaint(start+20.409);
            }""",dict(bars=bars,start=start))
            page.get_by_role('checkbox',name='Show level reaction',exact=True).check()
            page.locator('.level-reaction-label').last.wait_for(timeout=60000)
            page.locator('.reaction-book-settings summary').click()
            page.get_by_label('Level book source',exact=True).select_option('model')
            page.locator('.reaction-book-settings summary').click()
            page.locator('.reaction-book-settings summary').filter(has_text='104 historical').wait_for(timeout=60000)
            assert all(body['session_date']=='2026-08-21' and body['time_et'].startswith('04:15:') for _,body in calls)
            assert not page.get_by_text('Waiting for completed candles',exact=True).count()
            # Continuous 30ms redraws must not cancel/indefinitely debounce work.
            page.evaluate("""start=>{let step=21;window.reactionTimer=setInterval(()=>{
              window.reactionPaint(start+step+.409);step++;if(step>55)clearInterval(window.reactionTimer);
            },30);}""",start)
            page.wait_for_function("t=>[...document.querySelectorAll('.level-reaction-label')].some(n=>Number(n.dataset.close)>=t)",arg=start+50,timeout=60000)
            page.evaluate('t=>window.reactionPaint(t)',start+20.409)
            page.wait_for_function("t=>[...document.querySelectorAll('.level-reaction-label')].every(n=>Number(n.dataset.close)<=t)",arg=start+20,timeout=10000)
            page.locator('.reaction-book-settings summary').click()
            page.get_by_text('Current-day swing observations',exact=True).wait_for()
            output=Path(r'D:\TradingML\runtimes\reaction-level-model\clock-regression')
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'saved-debug-book-predictions.png'),full_page=True)
            (output/'requests.json').write_text(json.dumps(calls),encoding='utf-8')
            page.evaluate('window.reactionRoot.unmount()')
        finally:
            output=Path(r'D:\TradingML\runtimes\reaction-level-model\clock-regression')
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'last-state.png'),full_page=True)
            browser.close()
