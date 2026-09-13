"""Render recorded strategy zone geometry in the production chart."""
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'),'Managed frontend required')
class V7ZonePresentationTests(unittest.TestCase):
    def test_recorded_zone_lines(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                page=browser.new_page(viewport={'width':1500,'height':950})
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.evaluate("""async()=>{
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  const {strategyReferencePresentation:project}=await import('/src/features/canvas/strategyReferencePresentation.ts');
                  const base=1787301300;
                  const candles=Array.from({length:90},(_,i)=>({time:base+i,endTime:base+i+1,open:4.65+i*.003,close:4.65+(i+1)*.003,high:4.67+(i+1)*.003,low:4.64+i*.003}));
                  const rows=[0,30].map((i)=>({ticker:'TEST',event_time:new Date((base+i)*1000).toISOString(),chart_plan:{historical_hod_reference:{at:base+i,hod:5,zone_lower:4.7+i*.001,resistance_upper:4.8,changed:true}}}));
                  const refs=project(rows,'TEST',new Date((base+90)*1000).toISOString());
                  if(refs.length!==2||refs[1].zoneLower!==4.73)throw Error('Recorded zone geometry');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  (dom.default??dom).createRoot(node).render(React.createElement(ChartPanel,{ticker:'TEST',timeframe:'1s',timeframes:['1s'],baseHeight:650,settingsStorageKey:'v7-zone-review',visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],payload:{candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[],strategy_references:refs}}));
                }""")
                page.wait_for_timeout(400)
                output=Path(os.environ['V7_ZONE_REVIEW_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
                for theme,scale,width in [('light',1,1500),('dark',.8,1000),('light',1.25,1000)]:
                    page.set_viewport_size({'width':width,'height':950})
                    page.evaluate("""async([theme,s])=>{const d=document.documentElement;(await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);d.style.setProperty('--app-zoom-inverse',1/s);d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`)}""",[theme,scale])
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(output/f'zone-{theme}-{scale}.png'))
                self.assertEqual(errors,[])
            finally:browser.close()
