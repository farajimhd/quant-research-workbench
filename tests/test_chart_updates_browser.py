"""Run with CHART_BROWSER_TEST_URL against the managed frontend; no backtest runs."""
import os
import unittest


@unittest.skipUnless(os.environ.get("CHART_BROWSER_TEST_URL"), "Explicit browser review URL required")
class ChartUpdateBrowserTests(unittest.TestCase):
    def test_native_viewport_and_position_updates(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                page.goto(os.environ["CHART_BROWSER_TEST_URL"])
                page.evaluate("""async () => {
                  const React = (await import('/node_modules/.vite/deps/react.js')).default;
                  const dom = await import('/node_modules/.vite/deps/react-dom_client.js'); const createRoot = (dom.default ?? dom).createRoot;
                  const {ChartPanel} = await import('/src/app/components/ChartPanel.tsx');
                  const {mergeHistoricalChartPage,limitIndicatorRowsToLatest} = await import('/src/features/canvas/chartData.ts');
                  const bar = i => ({bar_start: new Date(1787311200000+i*1000).toISOString()});
                  const rolling = mergeHistoricalChartPage([bar(0),bar(1)],[],[bar(2)],[],2);
                  if (rolling.bars.length !== 2 || rolling.bars[1].bar_start !== bar(2).bar_start) throw Error('Full window stopped advancing');
                  const seed = {...bar(0),qmd_structure_unified_levels:[{unified_level_id:1}]};
                  if (!limitIndicatorRowsToLatest([seed,bar(1),bar(2)],1).includes(seed)) throw Error('Structural seed was evicted');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node); const root=createRoot(node);
                  window.paint=(count=50,high=7.6,pnl=20,stop=7,target=7.5) => root.render(React.createElement(ChartPanel,{
                    ticker:'JUNS',timeframe:'1s',timeframes:['1s'],baseHeight:620,settingsStorageKey:'viewport-regression',
                    visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                    payload:{candles:Array.from({length:count},(_,i)=>({time:1787311200+i,open:7.2,close:7.21,high:i===count-1?high:7.6,low:6.9})),
                      volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[],
                      timeline_events:[{id:'anchor',time:1787311230,kind:'split',label:'Anchor',ariaLabel:'Anchor',title:'Anchor'}]},
                    liveEntryLine:{price:7.2,quantity:1339,pnl,stopPrice:stop,targetPrices:[target]}}));
                  window.paint();
                }""")
                entry=page.locator('.live-entry-price-line:not(.live-position-protection-line)')
                page.wait_for_timeout(500)
                self.assertEqual(page.locator('.live-entry-price-line').count(),3)
                y=float(entry.evaluate('(e)=>parseFloat(e.style.top)'))
                anchor=page.locator('[data-chart-timeline-event-id="anchor"]')
                x=float(anchor.evaluate('(e)=>parseFloat(e.style.left)'))
                page.evaluate('window.paint(51,12)')
                page.wait_for_timeout(150)
                self.assertAlmostEqual(float(entry.evaluate('(e)=>parseFloat(e.style.top)')),y,places=2)
                self.assertLess(float(anchor.evaluate('(e)=>parseFloat(e.style.left)')),x)
                # Native y-axis drag changes the transform without any React update.
                page.mouse.move(1450,300);page.mouse.down();page.mouse.move(1450,400,steps=12)
                page.wait_for_timeout(100)
                dragged=float(entry.evaluate('(e)=>parseFloat(e.style.top)'))
                page.mouse.up()
                self.assertNotAlmostEqual(dragged,y,places=1)
                page.evaluate('window.paint(52,15,-12,7.1,7.55)');page.wait_for_timeout(150)
                self.assertAlmostEqual(float(entry.evaluate('(e)=>parseFloat(e.style.top)')),dragged,places=1)
                self.assertIn('P&L -$12.00',entry.inner_text())
                self.assertIn('7.55',page.locator('[data-role=target]').inner_text())
                self.assertIn('7.1',page.locator('[data-role=stop]').inner_text())
            finally:
                browser.close()

    def test_slow_structural_refresh_retains_book_and_forming_candle(self):
        import json
        from urllib.parse import parse_qs, urlparse
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                page=browser.new_page()
                pending=[]
                start="2026-08-21T11:21:30Z"
                bar={"bar_start":start,"bar_end":"2026-08-21T11:21:31Z","is_closed":True,"open":7.2,"high":7.4,"low":7.1,"close":7.3,"volume":100}
                level={"unified_level_id":1,"side":1,"price":7.35,"lower":7.35,"upper":7.35,"lifecycle":"active","timeframes":["1s"],"sources":[],"hold_probability":1,"hold_quality_score":1,"ticker_relative_quality_score":1,"ticker_relative_quality_status":"available","confirmed_at_ms":1787311290000,"created_at_ms":1787311290000}
                book={"bar_start":start,"qmd_structure_unified_levels":[level]}
                response={"history":[bar],"indicators":[book],"indicators_available":True,"has_more":False,"market_signal_events":[],"structure_events":[],"structure_level_history":[]}
                def history(route):
                    query=parse_qs(urlparse(route.request.url).query)
                    if query.get("stage")==["full"]: pending.append(route)
                    else: route.fulfill(json={**response,"indicators":[]})
                page.route('**/api/trading/canvas-chart/history?*',history)
                page.route('**/api/trading/canvas-chart/forming?*',lambda route: route.fulfill(json={"current":{**bar,"bar_start":"2026-08-21T11:21:31Z","bar_end":"2026-08-21T11:21:32Z","is_closed":False,"close":7.36}}))
                page.goto(os.environ["CHART_BROWSER_TEST_URL"])
                page.evaluate("""async()=>{
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {useCanvasHistoricalChart}=await import('/src/features/canvas/chartData.ts');
                  const {ChartPreview}=await import('/src/features/canvas/chartPresentation.tsx');
                  document.getElementById('root').style.display='none';const node=document.createElement('div');document.body.appendChild(node);
                  const ids=['indicator.qmd_unified_structure'];
                  function Fixture(){const [clock,setClock]=React.useState(Date.parse('2026-08-21T11:21:31.100Z'));window.changeClock=setClock;
                    const state=useCanvasHistoricalChart('JUNS','1s',clock,'2026-08-21',ids,false,true,'backtest',false);window.chartState=state;
                    return React.createElement(ChartPreview,{canvasId:'qa',instanceId:'qa',linkContext:{symbol:'JUNS'},changeAsOf:new Date(clock).toISOString(),
                      chartSettings:{timeframe:'1s',visibleIndicators:ids,showSplitEvents:false},liveChart:state,onChartSettingsChange:()=>{},onLinkContextChange:()=>{},symbolEditable:false});}
                  (dom.default??dom).createRoot(node).render(React.createElement(Fixture));
                }""")
                page.wait_for_timeout(400)
                self.assertTrue(pending)
                self.assertTrue(page.evaluate('window.chartState.bars.some(b=>b.is_closed===false)'))
                page.evaluate("window.changeClock(Date.parse('2026-08-21T11:21:32.100Z'))")
                page.wait_for_timeout(700)
                for route in pending[:]: route.fulfill(json=response)
                pending.clear()
                page.wait_for_timeout(250)
                self.assertTrue(page.evaluate('window.chartState.indicators.some(r=>r.qmd_structure_unified_levels?.length)'))
                if page.get_by_role('button', name='Expand legend',exact=True).count():
                    page.get_by_role('button', name='Expand legend',exact=True).first.click()
                self.assertIn('Unified structural level book',page.locator('body').inner_text())
                page.evaluate("window.changeClock(Date.parse('2026-08-21T11:21:33.100Z'))")
                page.wait_for_timeout(500)
                self.assertTrue(page.evaluate('window.chartState.indicators.some(r=>r.qmd_structure_unified_levels?.length)'))
                for route in pending: route.abort()
            finally:
                browser.close()
