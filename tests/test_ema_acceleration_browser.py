"""Causal numerical vectors and production chart settings through managed Vite."""
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'), 'Managed frontend URL required')
class EmaAccelerationBrowserTests(unittest.TestCase):
    def test_vectors_and_chart(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                page.evaluate("""async()=>{
                  const {emaAcceleration:f,emaState}=await import('/src/app/components/emaAcceleration.ts');
                  const check=(v,m)=>{if(!v)throw Error(m)};
                  [[1,1,'risingFaster'],[1,-1,'risingSlower'],[-1,-1,'fallingFaster'],[-1,1,'fallingSlower'],[0,1,'neutral'],[1,0,'neutral']].forEach(([s,a,state])=>check(emaState(s,a)===state,'Slope/curvature state'));
                  const bars=Array.from({length:10},(_,i)=>({time:i,close:10+i*i}));
                  const r=f(bars,1,'price-bar2',10,1);
                  check(r.length===8&&r.every(p=>p.value===2),'Quadratic curvature');
                  check(f(bars.map(b=>({...b,close:10})),3,'price-bar2',10,1).every(p=>p.value===0),'Flat EMA');
                  check(f(bars,3,'price-bar2',10,1)[0].time===4,'SMA warmup');
                  check(Math.abs(f(bars,3,'price-bar2',10,1)[0].value-5/3)<1e-12,'EMA vector');
                  check(f(bars.map(b=>({...b,time:b.time*5})),1,'price-second2',50,5).every(p=>Math.abs(p.value-.08)<1e-12),'Time normalization');
                  check(Math.abs(f(bars,1,'bps-bar2',10,1).at(-1).value-2/91*10000)<1e-10,'Bps normalization');
                  check(JSON.stringify(f(bars,3,'price-bar2',6,1))===JSON.stringify(f(bars.slice(0,6),3,'price-bar2',6,1)),'Future leaked');
                  check(f([...bars,{time:10,close:10000,isClosed:false}],3,'price-bar2',100,1).length===6,'Forming leaked');
                  check(f([...bars,{time:10,close:NaN},{time:11,close:0}],3,'price-bar2',100,1).every(p=>Number.isFinite(p.value)),'Invalid output');
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  const {CHART_INDICATORS}=await import('/src/features/canvas/configuration.ts');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  const root=(dom.default??dom).createRoot(node);
                  function Harness(){
                    const [tf,setTf]=React.useState('1s');
                    const [visible,setVisible]=React.useState(['indicator.ema_acceleration']);
                    const step=tf==='1s'?1:5,base=1787301300;
                    const price=i=>10+.002*i+.2*Math.sin(i/15);
                    const candles=Array.from({length:180/step},(_,i)=>({time:base+i*step,endTime:base+(i+1)*step,isClosed:true,open:price(i*step),close:price((i+1)*step),high:price((i+1)*step)+.05,low:price(i*step)-.05}));
                    return React.createElement(ChartPanel,{ticker:'TEST',timeframe:tf,timeframes:['1s','5s'],onTimeframeChange:setTf,onTickerChange:()=>{},onVisibleColumnsChange:setVisible,baseHeight:360,settingsStorageKey:'ema-test',visibleColumns:visible,featureOptions:[],indicatorOptions:[],displayItemOptions:CHART_INDICATORS,indicatorAsOf:new Date((base+180)*1000).toISOString(),payload:{candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[]}});
                  }
                  root.render(React.createElement(Harness));
                }""")
                page.get_by_role('button', name='Expand legend', exact=True).first.click()
                page.get_by_role('button', name='Configure EMA 7 second derivative', exact=True).click()
                page.get_by_label('EMA length', exact=True).fill('12')
                self.assertEqual(page.get_by_label('EMA length', exact=True).input_value(), '12')
                page.get_by_label('EMA derivative units').select_option('bps-second2')
                for label in ['Rising · accelerating','Rising · slowing','Falling · accelerating downward','Falling · slowing','Flat slope or unchanged slope']:
                    self.assertTrue(page.get_by_label(label, exact=True).is_visible())
                page.get_by_label('Rising · accelerating', exact=True).fill('#12ab34')
                self.assertEqual(page.get_by_label('Rising · accelerating', exact=True).input_value(), '#12ab34')
                output = Path(os.environ['EMA_REVIEW_OUTPUT']) if os.environ.get('EMA_REVIEW_OUTPUT') else None
                for theme, scale, width in [('light', 1, 1500), ('dark', .8, 1000), ('light', 1.25, 1000)]:
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.evaluate("""async([theme,s])=>{const d=document.documentElement;(await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);d.style.setProperty('--app-zoom-inverse',1/s);d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`)}""", [theme, scale])
                    page.wait_for_timeout(250)
                    box = page.locator('.ema-acceleration-editor').bounding_box()
                    self.assertLessEqual(box['x'] + box['width'], width + 1)
                    self.assertLessEqual(box['y'] + box['height'], 1001)
                    if output:
                        output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(output / f'ema-{theme}-{scale}.png'))
                page.get_by_role('button', name='Close presentation settings').click()
                before = page.locator('.chart-legend-row').filter(has_text='EMA 12 second derivative').inner_text()
                page.get_by_role('button', name='5s', exact=True).click()
                page.wait_for_timeout(250)
                after = page.locator('.chart-legend-row').filter(has_text='EMA 12 second derivative').inner_text()
                self.assertNotEqual(before, after)
                page.get_by_role('button', name='Configure EMA 12 second derivative', exact=True).click()
                self.assertEqual(page.get_by_label('EMA length', exact=True).input_value(), '12')
                self.assertEqual(page.get_by_label('EMA derivative units').input_value(), 'bps-second2')
            finally:
                browser.close()
