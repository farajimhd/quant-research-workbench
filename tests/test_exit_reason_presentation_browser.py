"""Issued-exit reasons through the production lifecycle projection and renderer."""
import os
from pathlib import Path
import pytest


@pytest.mark.skipif(not os.environ.get('CHART_BROWSER_TEST_URL'),reason='Managed frontend required')
def test_exit_reasons_at_issue_time_and_causal_cutoff():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1500,'height':950})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(os.environ['CHART_BROWSER_TEST_URL'])
            page.evaluate("""async()=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
              const {positionLifecycleAnnotations:project,shortExitReason}=await import('/src/features/canvas/chartPresentation.tsx');
              const base=1787301300, iso=i=>new Date((base+i)*1000).toISOString();
              const reasons=['red_close_below_attempt_open','macd_episode_ended','protective_stop'];
              const positions=reasons.map((_,i)=>({position_id:`p${i}`,instrument:{symbol:'TEST'},status:'closed',side:'LONG',
                opened_at:iso(i*8+1),closed_at:iso(i*8+7),entry_price:4.65+i*.07,exit_price:4.68+i*.07,exit_reason:'later_fill_reason',quantity:100,realized_pnl:3}));
              const activity=reasons.map((reason,i)=>({ticker:'TEST',event_type:'decision',action:'exit',reason,event_time:iso(i*8+5),reference_price:4.70+i*.07}));
              const trading={position_lifecycles:positions,strategy_chart_activity:activity,presentation_as_of:iso(24)};
              const trades=project(trading,'TEST');
              const intents=trades.flatMap(t=>t.exitIntents??[]);
              if(intents.length!==3||intents[0].time!==base+5||intents[0].label!=='Exit issued · Failed retest')throw Error('Issued exit reason/time');
              if(intents[1].label!=='Exit issued · MACD ended'||intents[2].label!=='Exit issued · Stop hit')throw Error('Reason mapping');
              if(project({...trading,presentation_as_of:iso(4)},'TEST').some(t=>t.exitIntents.length))throw Error('Future exit exposed');
              const missing=project({...trading,strategy_chart_activity:[{...activity[0],reason:''}]},'TEST')[0].exitIntents[0];
              if(!missing.label.includes('Reason unavailable')||missing.label.includes('later_fill'))throw Error('Future filled reason substituted');
              if(shortExitReason('protective_swing_failed')!=='Swing low failed')throw Error('Swing reason');
              const candles=Array.from({length:24},(_,i)=>({time:base+i,endTime:base+i+1,open:4.65+i*.003,close:4.65+(i+1)*.003,high:4.67+(i+1)*.003,low:4.64+i*.003}));
              window.__exitTexts=[];
              const fillText=CanvasRenderingContext2D.prototype.fillText;
              CanvasRenderingContext2D.prototype.fillText=function(text,...args){window.__exitTexts.push(String(text));return fillText.call(this,text,...args)};
              document.getElementById('root').style.display='none';
              const node=document.createElement('div');document.body.appendChild(node);
              (dom.default??dom).createRoot(node).render(React.createElement(ChartPanel,{ticker:'TEST',timeframe:'1s',timeframes:['1s'],baseHeight:650,
                settingsStorageKey:'exit-reason-review',visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                payload:{candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[],trade_annotations:trades}}));
            }""")
            output=Path(os.environ['EXIT_REASON_REVIEW_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
            for theme,scale,width in [('light',1,1500),('dark',.8,1000),('light',1.25,1000)]:
                page.set_viewport_size({'width':width,'height':950})
                page.evaluate("""async([theme,s])=>{const d=document.documentElement;(await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);d.style.setProperty('--app-zoom-inverse',1/s);d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`)}""",[theme,scale])
                page.wait_for_timeout(500)
                page.screenshot(path=str(output/f'exits-{theme}-{scale}.png'))
            texts=page.evaluate('window.__exitTexts')
            assert all(any(reason in text for text in texts) for reason in ['Failed retest','MACD ended','Stop hit'])
            assert not errors
        finally:browser.close()
