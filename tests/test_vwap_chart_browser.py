"""Exercise the real indicator projection and renderer against managed Vite."""
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'), 'Managed frontend URL required')
class VwapChartBrowserTests(unittest.TestCase):
    def test_missing_vwap_is_a_gap_and_zero_macd_is_valid(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                page=browser.new_page(viewport={'width':1500,'height':950})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                page.evaluate("""async()=>{
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  const {historicalIndicatorSeries}=await import('/src/features/canvas/chartPresentation.tsx');
                  const values=[4.10,4.11,null,4.12,undefined,4.13,NaN,4.14,Infinity,4.15,0,4.16,-1,4.17,'',4.18];
                  const rows=values.map((value,i)=>({bar_start:new Date((1787301300+i)*1000).toISOString(),execution_vwap:value,macd:0,macd_signal:0,macd_hist:0}));
                  const overlays=historicalIndicatorSeries(rows,'price',['indicator.vwap']);
                  const data=overlays[0].data;
                  if(data.length!==values.length)throw Error('Missing timestamps were removed');
                  values.forEach((v,i)=>{if(typeof v==='number'&&Number.isFinite(v)&&v>0){if(data[i].value!==v)throw Error('Valid VWAP changed');}else if(!Number.isNaN(data[i].value))throw Error('Invalid VWAP became a price');});
                  const oscillators=historicalIndicatorSeries(rows,'oscillator',['indicator.macd']);
                  if(!oscillators.some(s=>s.data.some(p=>p.value===0)))throw Error('Valid zero oscillator removed');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  const root=(dom.default??dom).createRoot(node);
                  window.paintVwap=()=>root.render(React.createElement(ChartPanel,{
                    ticker:'SUGP',timeframe:'1s',timeframes:['1s'],baseHeight:600,settingsStorageKey:'vwap-gap-review',
                    visibleColumns:['indicator.vwap','execution_vwap'],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                    payload:{candles:values.map((_,i)=>({time:1787301300+i,open:4.12,close:4.13,high:4.20,low:4.05})),volume:[],overlay_series:overlays,oscillator_series:[],markers:[],regions:[]}}));
                  window.paintVwap();
                }""")
                page.wait_for_timeout(600)
                self.assertGreater(page.locator('canvas').count(),0)
                # Repeated updates must also accept renderer whitespace.
                page.evaluate('window.paintVwap()')
                page.wait_for_timeout(150)
                self.assertEqual(errors,[])
                output=os.environ.get('VWAP_REVIEW_OUTPUT')
                if output:
                    target=Path(output);target.mkdir(parents=True,exist_ok=True)
                    for theme in ('light','dark'):
                        for scale,width in ((1.,900),(1.,1500)):
                            page.set_viewport_size({'width':width,'height':950})
                            page.evaluate("theme=>{document.documentElement.dataset.theme=theme}",theme)
                            page.wait_for_timeout(200)
                            page.screenshot(path=str(target/f'vwap-{theme}-{width}.png'))
            finally:
                browser.close()
