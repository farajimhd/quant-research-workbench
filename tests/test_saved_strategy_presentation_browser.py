"""Opt-in saved-run presentation through the real journal and chart renderer."""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
import pytest


@pytest.mark.skipif(not os.environ.get('SAVED_CHART_RUN'),reason='Saved run and captured QMD chart required')
def test_saved_run_exit_and_reference_geometry():
    from playwright.sync_api import sync_playwright
    from src.trading_runtime.journal import TradingJournal
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_activity_rows
    run=os.environ['SAVED_CHART_RUN']
    ticker=os.environ.get('SAVED_CHART_TICKER','SUGP')
    root=Path(os.environ['SAVED_CHART_RUNTIME'])
    data=json.load(urlopen(f'http://127.0.0.1:8000/api/trading/backtest/runs/{run}/canvas?symbol={ticker}'))
    journal=TradingJournal(root/'journal.sqlite3',read_only=True)
    try:
        rows=strategy_activity_payload(journal=journal,run_id=run,ticker=ticker,limit=50000,
            consequential_only=True,include_decision_evidence=False)['rows']
    finally:journal.close()
    data['trading']['strategy_chart_activity']=_compact_strategy_chart_activity_rows(rows)
    candles=json.loads(Path(os.environ['SAVED_CHART_BARS']).read_text())['history']
    data['candles']=[dict(time=datetime.fromisoformat(r['bar_start'].replace('Z','+00:00')).timestamp(),
        endTime=datetime.fromisoformat(r['bar_end'].replace('Z','+00:00')).timestamp(),
        **{k:r[k] for k in ('open','high','low','close')}) for r in candles]
    output=Path(os.environ['SAVED_CHART_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1500,'height':950})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(os.environ['CHART_BROWSER_TEST_URL'])
            projected=page.evaluate("""async ({data,ticker})=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
              const {positionLifecycleAnnotations}=await import('/src/features/canvas/chartPresentation.tsx');
              const {strategyReferencePresentation}=await import('/src/features/canvas/strategyReferencePresentation.ts');
              const trades=positionLifecycleAnnotations(data.trading,ticker);
              const trade=trades[1];
              const refs=strategyReferencePresentation(data.trading.strategy_chart_activity,ticker,data.trading.presentation_as_of);
              const candles=data.candles.filter(c=>c.time>=trade.entryTime-12&&c.time<=trade.exitTime+15);
              window.__texts=[];
              const orig=CanvasRenderingContext2D.prototype.fillText;
              CanvasRenderingContext2D.prototype.fillText=function(text,...args){window.__texts.push(String(text));return orig.call(this,text,...args)};
              document.getElementById('root').style.display='none';
              const node=document.createElement('div');document.body.appendChild(node);
              node.style.cssText='zoom:var(--app-zoom);width:calc(100vw / var(--app-zoom));height:calc(100vh / var(--app-zoom))';
              (dom.default??dom).createRoot(node).render(React.createElement(ChartPanel,{ticker,timeframe:'1s',timeframes:['1s'],baseHeight:650,
                settingsStorageKey:'saved-chart-review',strategyPresentationEnabled:true,visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                payload:{candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[],
                  strategy_references:refs,trade_annotations:[trade]}}));
              return {exit:trade.exitIntents,hod:trade.highOfDayPrice,resistances:trade.resistancePrices,refs:refs.length};
            }""",{'data':data,'ticker':ticker})
            assert projected['hod'] and projected['resistances'] and projected['refs']
            assert projected['exit'][0]['label']=='Exit issued · Failed retest'
            for theme,scale,width in [('light',1,1500),('dark',.8,1000),('light',1.25,1000)]:
                page.set_viewport_size({'width':width,'height':950})
                page.evaluate("""async([theme,s])=>{const d=document.documentElement;
                  (await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);
                  d.style.setProperty('--app-zoom-inverse',1/s);d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);
                  d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`)}""",[theme,scale])
                page.wait_for_timeout(800)
                page.screenshot(path=str(output/f'saved-{theme}-{scale}.png'))
                texts=page.evaluate('window.__texts')
                assert any('Failed retest' in t for t in texts),texts[-30:]
                assert any('HOD ' in t for t in texts),texts[-30:]
                assert any('Entry zone floor ' in t for t in texts),texts[-30:]
                assert any('Entry R ' in t for t in texts),texts[-30:]
            assert not errors
        finally:browser.close()


@pytest.mark.skipif(not os.environ.get('SAVED_CHART_RUN'),reason='Saved run required')
def test_full_canvas_fetches_activity_for_displayed_symbol():
    """AAPL workspace scope must not suppress SUGP's independent chart scope."""
    from playwright.sync_api import sync_playwright
    run=os.environ['SAVED_CHART_RUN']
    ticker=os.environ.get('SAVED_CHART_TICKER','SUGP')
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1500,'height':950})
            page.goto(f"{os.environ['CHART_BROWSER_TEST_URL']}/?replay_run={run}&historical_mode=backtest#canvas-focus")
            # Inspect the actual ChartPanel inputs; no replacement payload,
            # direct component mounting or mocked activity endpoint here.
            inspect="""()=>{const el=document.querySelector('#root');if(!el)return [];
              const root=el[Object.keys(el).find(k=>k.startsWith('__reactContainer'))];
              const result=[],seen=new Set();function visit(f){if(!f||seen.has(f))return;seen.add(f);
                const p=f.memoizedProps;if(p?.payload?.candles&&p?.ticker)result.push({ticker:p.ticker,
                  refs:p.payload.strategy_references?.length??0,
                  exits:(p.payload.trade_annotations??[]).flatMap(t=>t.exitIntents??[]).length});
                visit(f.child);visit(f.sibling);if(f===root)visit(f.alternate)}visit(root);return result}
            """
            page.wait_for_function(f"() => ({inspect})().some(p=>p.ticker==={json.dumps(ticker)}&&p.refs>0&&p.exits>0)",timeout=30000)
            page.screenshot(path=str(Path(os.environ['SAVED_CHART_OUTPUT'])/'full-canvas.png'))
        finally:browser.close()
