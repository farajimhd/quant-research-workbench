"""Opt-in real saved lifecycle projection and stop/target renderer review."""
import json
import os
from pathlib import Path
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(not os.environ.get('SQUEEZE_CHART_REVIEW'), reason='Requires frontend and saved JUNS run')
def test_saved_squeeze_protection_movement():
    from playwright.sync_api import sync_playwright
    data = json.load(urlopen('http://127.0.0.1:8000/api/trading/backtest/runs/'
                            '0868a5ee-d4b8-4c11-8dbd-0828a81eee21/canvas?symbol=JUNS'))
    output = Path(os.environ['SQUEEZE_CHART_REVIEW'])
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':1500, 'height':950})
            page.goto(os.environ.get('CHART_BROWSER_TEST_URL','http://127.0.0.1:5174'))
            actual = page.evaluate("""async data => {
              const {positionLifecycleAnnotations}=await import('/src/features/canvas/chartPresentation.tsx');
              const annotations=positionLifecycleAnnotations(data.trading,'JUNS');
              const trade=annotations.find(t=>new Set(t.protectionPath?.filter(p=>p.kind==='stop'&&p.active).map(p=>p.price)).size>1);
              if(!trade)throw Error('Saved JUNS stop movement missing');
              return {stops:trade.protectionPath.filter(p=>p.kind==='stop'&&p.active).map(p=>p.price),
                targets:trade.protectionPath.filter(p=>p.kind==='target'&&p.active).map(p=>p.price)};
            }""",data)
            assert len(set(actual['stops'])) > 1 and actual['targets']
            # Controlled minute-long geometry makes both movement paths legible.
            # This renderer fixture is separate from the real-journal assertion above.
            page.evaluate("""async () => {
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
              const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
              document.getElementById('root').style.display='none';
              const host=document.createElement('div');document.body.append(host);
              host.style.cssText='zoom:var(--app-zoom);width:calc(100vw / var(--app-zoom));height:calc(100vh / var(--app-zoom))';
              const t=1787311200;
              const candles=Array.from({length:65},(_,i)=>({time:t+i,open:10+i*.01,
                close:10.01+i*.01,high:10.03+i*.01,low:9.98+i*.01}));
              const path=[{time:t+5,sequence:1,orderId:'s',kind:'stop',price:9.8,active:true},
                {time:t+5,sequence:2,orderId:'p',kind:'target',price:10.8,active:true},
                {time:t+20,sequence:3,orderId:'s',kind:'stop',price:10,active:true},
                {time:t+20,sequence:4,orderId:'p',kind:'target',price:11,active:true},
                {time:t+40,sequence:5,orderId:'s',kind:'stop',price:10.2,active:true},
                {time:t+40,sequence:6,orderId:'p',kind:'target',price:11.2,active:true}];
              window.__protectionTexts=[];
              const old=CanvasRenderingContext2D.prototype.fillText;
              CanvasRenderingContext2D.prototype.fillText=function(s,...args){window.__protectionTexts.push(String(s));return old.call(this,s,...args)};
              dom.createRoot(host).render(React.createElement(ChartPanel,{ticker:'JUNS',timeframe:'1s',timeframes:['1s'],
                baseHeight:600,settingsStorageKey:'squeeze-v5-review',strategyPresentationEnabled:true,
                visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                payload:{candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[],
                  trade_annotations:[{id:'movement',entryTime:t+5,entryPrice:10.05,entryIntentTime:t+5,
                    entryIntentPrice:10.05,endTime:t+60,exitTime:t+60,exitPrice:10.6,status:'closed',
                    positionSide:'LONG',protectionPath:path}]}}));
            }""")
            for theme, scale, width in [('light',1,1500),('dark',.8,1000),('light',1.25,1000)]:
                page.set_viewport_size({'width':width,'height':950})
                page.evaluate("""async ([theme,s])=>{
                  const d=document.documentElement;(await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);
                  d.style.setProperty('--app-zoom',s);d.style.setProperty('--app-zoom-inverse',1/s);
                  d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);
                  d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`);
                  window.__protectionTexts=[];
                }""",[theme,scale])
                page.wait_for_timeout(900)
                text=page.evaluate('window.__protectionTexts')
                assert any(s.startswith('SL ') for s in text),text
                assert any(s.startswith('TP ') for s in text),text
                page.screenshot(path=str(output/f'{theme}-{scale}.png'))
        finally:
            browser.close()
