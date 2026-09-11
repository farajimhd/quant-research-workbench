"""Run against the managed frontend with CHART_BROWSER_TEST_URL set."""
import os
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'), 'Managed frontend URL required')
class SupertrendTests(unittest.TestCase):
    def test_wilder_vectors_closed_prefix_and_native_overlay(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                page=browser.new_page(viewport={'width':1500,'height':950})
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                page.evaluate("""async () => {
                  const {supertrend}=await import('/src/app/components/supertrend.ts');
                  const check=(x,msg)=>{if(!x)throw Error(msg)};
                  const bars=[0,1,2].map(time=>({time,open:10,close:10,high:11,low:9}));
                  bars.push({time:3,open:10,close:13,high:14,low:10},
                    {time:4,open:13,close:14,high:15,low:12},{time:5,open:14,close:8,high:14,low:7});
                  const r=supertrend(bars,3,1);
                  const {supertrendLineData}=await import('/src/app/components/SupertrendIndicator.tsx');
                  const line=supertrendLineData(r,'green','red');
                  check(JSON.stringify(line.map(p=>p.color))==='["transparent","green","transparent","red"]','Flip bridges or inactive line');
                  [12,28/3,193/18,793/54].forEach((v,i)=>check(Math.abs(r[i].value-v)<1e-10,'Wilder/band vector '+i));
                  check(JSON.stringify(r.map(p=>p.direction))==='[-1,1,1,-1]','Both flips');
                  check(JSON.stringify(supertrend(bars,3,1,5))===JSON.stringify(r.slice(0,3)),'Forming candle leaked');
                  check(JSON.stringify(supertrend(bars.slice(0,5),3,1))===JSON.stringify(r.slice(0,3)),'Prefix changed');
                  const equal=[...bars.slice(0,3),{time:3,open:12,close:12,high:13,low:11}];
                  check(supertrend(equal,3,1).at(-1).direction===-1,'Equality flipped');
                  check(supertrend(bars.slice(0,2),3,1).length===0,'Warmup');
                  const flat=[10,11,11].map((p,time)=>({time,open:p,high:p,low:p,close:p}));
                  check(JSON.stringify(supertrend(flat,1,1).map(p=>p.direction))==='[-1,1,-1]','Collapsed band tie rule');
                  for(const period of [0,2.5,501]) {let rejected=false;try{supertrend(bars,period)}catch{rejected=true}check(rejected,'Invalid period');}
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div');document.body.appendChild(node);
                  const root=(dom.default??dom).createRoot(node);
                  window.stRoot=root;localStorage.removeItem('supertrend-test.supertrend');
                  window.stPaint=(timeframe='1s')=>root.render(React.createElement(ChartPanel,{
                    ticker:'TEST',timeframe,timeframes:['1s','5s'],baseHeight:620,settingsStorageKey:'supertrend-test',
                    visibleColumns:[],featureOptions:[],indicatorOptions:[],displayItemOptions:[],
                    payload:{candles:Array.from({length:80},(_,i)=>{const close=10+Math.sin(i/8);return {time:1787311200+i,open:close-.02,high:close+.1,low:close-.1,close}}),
                    volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[]}}));
                  window.stPaint();
                }""")
                control=page.get_by_role('button',name='Supertrend 10 × 3',exact=False)
                control.wait_for(state='visible')
                self.assertTrue(control.evaluate('(e)=>e.scrollWidth<=e.clientWidth && e.scrollHeight<=e.clientHeight'))
                control.click()
                page.get_by_label('Supertrend ATR period',exact=True).fill('7')
                page.get_by_label('Supertrend multiplier',exact=True).fill('2')
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('supertrend-test.supertrend')).period"),7)
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('supertrend-test.supertrend')).multiplier"),2)
                page.get_by_role('button',name='Close Supertrend settings',exact=True).click()
                page.get_by_role('button',name='Supertrend 7 × 2',exact=False).wait_for(state='visible')
                toggle=page.get_by_role('checkbox',name='Show 1s Supertrend',exact=True)
                toggle.uncheck()
                control.wait_for(state='hidden')
                self.assertFalse(page.evaluate("JSON.parse(localStorage.getItem('supertrend-test.supertrend')).enabled"))
                toggle.check()
                page.get_by_role('button',name='Supertrend 7 × 2',exact=False).wait_for(state='visible')
                page.evaluate("window.stPaint('5s')")
                page.get_by_role('button',name='Supertrend 7 × 2',exact=False).wait_for(state='hidden')
                page.evaluate("window.stPaint('1s')")
                page.get_by_role('button',name='Supertrend 7 × 2',exact=False).wait_for(state='visible')
                page.evaluate('window.stRoot.unmount()')
            finally:
                browser.close()
