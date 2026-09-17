"""Canonical-state vectors and shared-chart multi-timeframe interaction checks."""
import json
import os
import re
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'), 'Managed frontend URL required')
class FormingMacdBrowserTests(unittest.TestCase):
    def test_projection_and_shared_chart(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route(re.compile(r'^https?://[^/]+/api/'), lambda route: route.fulfill(json={'data': {}}))
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                result = page.evaluate("""async () => {
                  const {projectFormingMacd:f,loadMacdSource}=await import('/src/features/canvas/formingMacd.ts');
                  const check=(v,m)=>{if(!v)throw Error(m)};
                  const near=(a,b,m)=>check(Math.abs(a-b)<1e-10,m+': '+a+' != '+b);
                  let fast=10,slow=10,signal=0;
                  const source=[],states=[];
                  for(let i=0;i<80;i++){
                    const close=10+Math.sin(i/7)+i*.02;
                    fast=2/13*close+11/13*fast;slow=2/27*close+25/27*slow;
                    const line=fast-slow;signal=.2*line+.8*signal;
                    source.push({start:i*5,end:(i+1)*5,close,line,signal});states.push({fast,slow,signal});
                  }
                  const samples=[{time:150,endTime:151,close:12},{time:151,endTime:152,close:14},{time:154,endTime:155,close:99}];
                  const projected=f(samples,{rows:source,through:400},1,155);
                  for(let i=0;i<2;i++){
                    const b=states[29],p=samples[i].close;
                    const line=2/13*p+11/13*b.fast-(2/27*p+25/27*b.slow);
                    near(projected.points[i].value,.8*(line-b.signal),'Independent forming preview');
                  }
                  near(projected.points[2].value,source[30].line-source[30].signal,'Exact completed source boundary');
                  const changed=source.map(r=>r.end>152?{...r,line:999,signal:-999,close:999}:r);
                  check(JSON.stringify(f(samples,{rows:changed,through:400},1,152))===JSON.stringify(f(samples,{rows:source,through:400},1,152)),'Future leaked');
                  near(f([{time:150,endTime:152,close:14}],{rows:source,through:400},2,152).points[0].value,projected.points[1].value,'Chart timeframe changed source MACD');
                  check(f([{time:150,endTime:155,close:999}],{rows:source,through:400},5,152).points.length===0,'Future chart close leaked');
                  near(f([{time:150,endTime:155,isClosed:false,close:14}],{rows:source,through:400},5,152).points[0].value,projected.points[1].value,'Forming chart cursor');
                  check(Number.isNaN(f(samples,{rows:source,through:150},1,155).points[0].value),'Stale source extrapolated');
                  check(Number.isNaN(f(samples,{rows:[] ,through:400},1,155).points[0].value),'Missing seed fabricated');
                  check(Number.isNaN(f([{time:1,close:NaN}],{rows:source,through:400},1,400).points[0].value),'Invalid price accepted');
                  const adjusted={rows:source.map(r=>({...r,close:r.close/2,line:r.line/2,signal:r.signal/2})),through:400,splitAdjusted:true,basisAsOf:400,adjustments:[{effective_at:new Date(300000).toISOString(),split_from:1,split_to:2}]};
                  near(f(samples,adjusted,1,155).points[0].value,projected.points[0].value,'Raw chart restored from adjusted source');
                  near(f(samples.map(r=>({...r,close:r.close/2})),adjusted,1,155,true).points[0].value,projected.points[0].value/2,'Adjusted chart/source parity');
                  const rawWithBasis={rows:source,through:400,basisAsOf:400,adjustments:adjusted.adjustments};
                  near(f(samples.map(r=>({...r,close:r.close/2})),rawWithBasis,1,155,true).points[0].value,projected.points[0].value/2,'Intraday source on adjusted chart');

                  // Fixture at the HTTP contract seam; production loader,
                  // React controls and native chart renderer remain real.
                  const nativeFetch=window.fetch.bind(window);window.macdRequests=[];
                  const pageFor=indices=>({history:indices.map(i=>({bar_start:new Date(i*5000).toISOString(),bar_end:new Date((i+1)*5000).toISOString(),close:source[i].close,is_closed:true})),indicators:indices.map(i=>({bar_start:new Date(i*5000).toISOString(),macd_line:source[i].line,macd_signal:source[i].signal})),has_more:false});
                  let queue=[{...pageFor([30,31]),has_more:true,next_before:'1970-01-01T00:02:30Z',earliest_session_date:'1970-01-01'},pageFor([28,29])],calls=[];
                  window.fetch=async input=>{calls.push(String(input));return new Response(JSON.stringify(queue.shift()),{status:200})};
                  const loaded=await loadMacdSource('TEST','5s',150,160,new AbortController().signal);
                  check(loaded.rows.length===4 && calls.length===2 && calls[1].includes('before_bar='),'Canonical pagination and warm seed');
                  check(loaded.through>164 && loaded.through<165,'Source-boundary reuse');
                  queue=[{...pageFor([28,29,30]),indicators:[]}];let rejected=false;
                  try{await loadMacdSource('TEST','5s',150,160,new AbortController().signal)}catch(e){rejected=String(e).includes('authoritative MACD is missing')}
                  check(rejected,'Missing MACD source silently skipped');
                  queue=[{...pageFor([30]),has_more:true,next_before:'same',earliest_session_date:'1970-01-01'},{...pageFor([30]),has_more:true,next_before:'same',earliest_session_date:'1970-01-01'}];rejected=false;
                  try{await loadMacdSource('TEST','5s',150,160,new AbortController().signal)}catch(e){rejected=String(e).includes('did not advance')}
                  check(rejected,'Repeated cursor accepted');
                  window.fetch=async(input,init)=>{
                    const url=new URL(String(input),location.origin);
                    if(url.pathname==='/api/trading/canvas-chart/macd-source')return new Response(JSON.stringify({rows:source.map(r=>({...r,start:r.start+1787300000,end:r.end+1787300000})),through:1787301180,splitAdjusted:true,adjustments:[]}),{status:200});
                    if(url.pathname!=='/api/trading/canvas-chart/history')return nativeFetch(input,init);
                    const tf=url.searchParams.get('timeframe'),d={'1s':1,'5s':5,'1m':60,'5m':300}[tf];
                    window.macdRequests.push(tf);
                    if(!d)return new Response(JSON.stringify({history:[],indicators:[],has_more:false}),{status:200});
                    const end=Date.parse(url.searchParams.get('as_of'))/1000,base=1787301000;
                    const history=[],indicators=[];let fast=10,slow=10,signal=0;
                    for(let t=base-360*d;t+d<=end;t+=d){
                      const close=10+.4*Math.sin((t-base)/20)+.001*(t-base)/d;
                      fast=2/13*close+11/13*fast;slow=2/27*close+25/27*slow;
                      const line=fast-slow;signal=.2*line+.8*signal;
                      const bar_start=new Date(t*1000).toISOString();
                      history.push({bar_start,bar_end:new Date((t+d)*1000).toISOString(),is_closed:true,open:close,close,low:close-.1,high:close+.1,volume:1});
                      indicators.push({bar_start,macd_line:line,macd_signal:signal});
                    }
                    return new Response(JSON.stringify({history,indicators,has_more:false,indicators_available:true}),{status:200,headers:{'Content-Type':'application/json'}});
                  };
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  const root=(dom.default??dom).createRoot(node);window.macdRoot=root;
                  localStorage.setItem('macd-test.forming-macd',JSON.stringify({enabled:false,timeframes:['1s','5s']}));
                  function Harness(){
                    const [tf,setTf]=React.useState('1s'),base=1787301000,d=tf==='1s'?1:5;
                    const candles=React.useMemo(()=>Array.from({length:180/d},(_,i)=>({time:base+i*d,endTime:base+(i+1)*d,isClosed:true,open:10,close:10+.4*Math.sin(i*d/20),high:10.5,low:9.5})),[tf]);
                    return React.createElement(ChartPanel,{ticker:'TEST',timeframe:tf,timeframes:['1s','5s'],onTimeframeChange:setTf,onTickerChange:()=>{},onVisibleColumnsChange:()=>{},baseHeight:380,settingsStorageKey:'macd-test',visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],indicatorAsOf:new Date((base+180)*1000).toISOString(),payload:{timeframe:tf,candles,volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[]}});
                  }
                  root.render(React.createElement(Harness));return {vectors:9};
                }""")
                self.assertEqual(result['vectors'], 9)
                page.get_by_role('button', name=re.compile(r'^Indicators')).click()
                page.get_by_label('Multi-timeframe MACD difference', exact=True).check()
                pane_close = page.get_by_role('button', name='Close Multi-timeframe MACD difference pane', exact=True)
                pane_close.wait_for(state='visible')
                page.get_by_role('button', name='Configure multi-timeframe MACD', exact=True).click()
                page.get_by_text('180 points', exact=False).first.wait_for(state='visible')
                self.assertTrue(page.get_by_label('MACD source 1s', exact=True).is_checked())
                self.assertTrue(page.get_by_label('MACD source 5s', exact=True).is_checked())
                page.get_by_label('MACD source 1m', exact=True).check()
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('macd-test.forming-macd')).timeframes"), ['1s', '5s', '1m'])
                output = Path(os.environ['MACD_REVIEW_OUTPUT'])
                output.mkdir(parents=True, exist_ok=True)
                for theme, scale, width in [('light', 1, 1500), ('dark', .8, 1000), ('light', 1.25, 1000)]:
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.evaluate("""async([theme,s])=>{const d=document.documentElement;(await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);d.style.setProperty('--app-zoom-inverse',1/s);d.style.setProperty('--app-zoomed-viewport-height',`${100/s}vh`);d.style.setProperty('--app-zoomed-viewport-width',`${100/s}vw`)}""", [theme, scale])
                    page.wait_for_timeout(200)
                    box = page.get_by_role('dialog', name='Multi-timeframe MACD difference', exact=True).bounding_box()
                    self.assertLessEqual(box['x']+box['width'], width+1)
                    self.assertLessEqual(box['y']+box['height'], 1001)
                    page.screenshot(path=str(output / f'macd-settings-{theme}-{scale}.png'))
                page.keyboard.press('Escape')
                page.get_by_role('button', name=re.compile(r'^Indicators')).click()
                page.get_by_role('button', name='Expand legend', exact=True).last.click()
                self.assertTrue(page.get_by_role('button', name='Configure MACD − signal · 1s', exact=True).is_visible())
                self.assertTrue(page.get_by_role('button', name='Configure MACD − signal · 5s', exact=True).is_visible())
                self.assertTrue(page.get_by_role('button', name='Configure MACD − signal · 1m', exact=True).is_visible())
                page.screenshot(path=str(output / 'macd-three-lines.png'))
                page.get_by_role('button', name='5s', exact=True).click()
                page.get_by_role('button', name='MACD difference · 1s,5s,1m', exact=True).click()
                self.assertTrue(page.get_by_label('MACD source 1s', exact=True).is_checked())
                page.get_by_label('MACD source 1d', exact=True).check()
                self.assertTrue(page.get_by_label('MACD source 1d', exact=True).is_checked())
                page.wait_for_function("JSON.parse(localStorage.getItem('macd-test.forming-macd')).timeframes.includes('1d')")
                page.get_by_label('MACD source 1d', exact=True).uncheck()
                page.keyboard.press('Escape')
                pane_close.click()
                pane_close.wait_for(state='hidden')
                self.assertFalse(page.evaluate("JSON.parse(localStorage.getItem('macd-test.forming-macd')).enabled"))
                page.get_by_role('button', name=re.compile(r'^Indicators')).click()
                page.get_by_label('Multi-timeframe MACD difference', exact=True).check()
                pane_close.wait_for(state='visible')
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('macd-test.forming-macd')).timeframes"), ['1s', '5s', '1m'])
                page.get_by_role('button', name='Configure multi-timeframe MACD', exact=True).click()
                for tf in ['1d', '1w', '1mo', '1y']:
                    page.get_by_label(f'MACD source {tf}', exact=True).check()
                for tf in ['1s', '5s', '1m']:
                    page.get_by_label(f'MACD source {tf}', exact=True).uncheck()
                page.keyboard.press('Escape')
                page.get_by_role('button', name=re.compile(r'^Indicators')).click()
                if page.get_by_role('button', name='Expand legend', exact=True).count():
                    page.get_by_role('button', name='Expand legend', exact=True).last.click()
                for tf in ['1d', '1w', '1mo', '1y']:
                    page.get_by_role('button', name=f'Configure MACD − signal · {tf}', exact=True).wait_for(state='visible')
                page.screenshot(path=str(output / 'macd-four-calendar-lines.png'))
                page.evaluate('window.macdRoot.unmount()')
                (output / 'result.json').write_text(json.dumps({'vectors': 9, 'themes': ['light', 'dark'], 'scales': [.8, 1, 1.25], 'errors': errors}), encoding='utf-8')
                self.assertEqual(errors, [])
            finally:
                browser.close()
