"""V6 origin projection and persisted color controls on the managed frontend."""
import os
import unittest


@unittest.skipUnless(os.environ.get('CHART_BROWSER_TEST_URL'), 'Managed frontend URL required')
class V6ChartColorsTests(unittest.TestCase):
    def test_session_origin_colors_and_persistence(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 950})
                page.goto(os.environ['CHART_BROWSER_TEST_URL'])
                page.evaluate("""async () => {
                  const {historicalMarketLevelZones}=await import('/src/features/canvas/chartPresentation.tsx');
                  const React=(await import('/node_modules/.vite/deps/react.js')).default;
                  const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
                  const base=Date.parse('2026-08-21T13:30:00Z')/1000;
                  const bars=Array.from({length:80},(_,i)=>({bar_start:new Date((base+i)*1000).toISOString(),
                    bar_end:new Date((base+i+1)*1000).toISOString(),session_date:'2026-08-21',
                    open:10,high:11,low:9,close:10+Math.sin(i/8)*.5}));
                  const levels=[1,1,-1,-1].map((side,i)=>({unified_level_id:(side===1?'s:':'r:')+String(i).padStart(16,'0'),
                    side,price:9.3+i*.4,lower:9.25+i*.4,upper:9.35+i*.4,prominence:70,
                    created_at_ms:Date.parse(i%2?'2026-08-21T12:00:00Z':'2026-08-21T00:30:00Z'),
                    confirmed_at_ms:Date.parse(i%2?'2026-08-21T12:01:00Z':'2026-08-21T00:31:00Z'),
                    lifecycle:'active',book_version:'causal-swing-closing-book-6',
                    load_contract:'symmetric-level-evidence-selection-2',sources:[],timeframes:['1s']}));
                  const indicators=[{...bars[0],qmd_structure_unified_levels:levels}];
                  const zones=historicalMarketLevelZones(indicators,bars,[],[],['indicator.qmd_unified_structure'],'1s');
                  const expected=['historicalSupport','currentSupport','historicalResistance','currentResistance'];
                  if(JSON.stringify(zones.map(z=>z.v6Category))!==JSON.stringify(expected))throw Error('New York origin classification');
                  const merged=historicalMarketLevelZones([{...indicators[0],qmd_structure_unified_levels:levels.map(l=>({...l,
                    created_at_ms:Date.parse('2026-08-21T11:18:12Z'),confirmed_at_ms:Date.parse('2026-08-21T11:18:27Z'),
                    oldest_member_confirmed_at_ms:l.confirmed_at_ms}))}],bars,[],[],['indicator.qmd_unified_structure'],'1s');
                  if(JSON.stringify(merged.map(z=>z.v6Category))!==JSON.stringify(expected))throw Error('Merged historical provenance lost');
                  const next=historicalMarketLevelZones(indicators,bars.map(b=>({...b,bar_start:b.bar_start.replace('2026-08-21','2026-08-24'),bar_end:b.bar_end.replace('2026-08-21','2026-08-24')})),[],[],['indicator.qmd_unified_structure'],'1s');
                  if(next.some(z=>!z.v6Category.startsWith('historical')))throw Error('Replay session classification');
                  const legacy=historicalMarketLevelZones([{...indicators[0],qmd_structure_unified_levels:levels.map(l=>({...l,book_version:'causal-swing-closing-book-5'}))}],bars,[],[],['indicator.qmd_unified_structure'],'1s');
                  if(legacy.some(z=>z.v6Category))throw Error('Changed V5');
                  document.getElementById('root').style.display='none';
                  const host=document.createElement('div');document.body.appendChild(host);
                  window.v6Root=(dom.default??dom).createRoot(host);
                  localStorage.removeItem('v6-test.legend');
                  window.v6Paint=()=>window.v6Root.render(React.createElement(ChartPanel,{
                    ticker:'TEST',timeframe:'1s',timeframes:['1s'],baseHeight:620,settingsStorageKey:'v6-test',
                    visibleColumns:['indicator.qmd_unified_structure'],featureOptions:[],indicatorOptions:[],displayItemOptions:[],onVisibleColumnsChange:()=>{},
                    payload:{candles:bars.map(b=>({time:Date.parse(b.bar_start)/1000,...b})),price_zones:zones,
                      volume:[],overlay_series:[],oscillator_series:[],markers:[],regions:[]}}));
                  window.v6Remount=()=>{window.v6Root.unmount();window.v6Root=(dom.default??dom).createRoot(host);window.v6Paint();};
                  window.v6Paint();
                }""")
                page.locator('.chart-shell').wait_for()
                expand = page.get_by_role('button', name='Expand legend', exact=True)
                if expand.count():
                    expand.click()
                page.get_by_role('button', name='Configure Swing level book v6', exact=True).click()
                colors = ['Historical resistance', 'Current-day resistance', 'Historical support', 'Current-day support']
                defaults = [page.get_by_label(label, exact=True).input_value() for label in colors]
                self.assertEqual(len(set(defaults)), 4)
                for label, color in zip(colors, ['#aa1122', '#ee4455', '#116633', '#44bb77']):
                    page.get_by_label(label, exact=True).fill(color)
                stored = page.evaluate("JSON.parse(localStorage.getItem('v6-test.legend'))['price-zone:indicator.qmd_unified_structure.v5'].v6Colors")
                self.assertEqual(stored, dict(historicalResistance='#aa1122', currentResistance='#ee4455', historicalSupport='#116633', currentSupport='#44bb77'))
                page.wait_for_timeout(100)
                baseline = page.locator('.chart-price canvas').evaluate_all('(canvases)=>canvases.map(c=>c.toDataURL())')
                for label in colors:
                    toggle = page.get_by_role('checkbox', name='Show ' + label.lower(), exact=True)
                    toggle.uncheck()
                    self.assertFalse(toggle.is_checked())
                hidden = page.evaluate("JSON.parse(localStorage.getItem('v6-test.legend'))['price-zone:indicator.qmd_unified_structure.v5'].v6Hidden")
                self.assertEqual(len(hidden), 4)
                page.wait_for_timeout(100)
                self.assertTrue(baseline != page.locator('.chart-price canvas').evaluate_all('(canvases)=>canvases.map(c=>c.toDataURL())'), 'Hiding all categories must change the native chart')
                page.evaluate('window.v6Remount()')
                page.get_by_role('checkbox', name='Show 1s Supertrend', exact=True).wait_for()
                expand = page.get_by_role('button', name='Expand legend', exact=True)
                if expand.count():
                    expand.click()
                page.get_by_role('button', name='Configure Swing level book v6', exact=True).click()
                for label in colors:
                    self.assertFalse(page.get_by_role('checkbox', name='Show ' + label.lower(), exact=True).is_checked())
                for label in colors:
                    page.get_by_role('checkbox', name='Show ' + label.lower(), exact=True).check()
                page.keyboard.press('Escape')
                page.get_by_role('button', name='Configure Swing level book v6', exact=True).click()
                self.assertEqual(page.get_by_label('Historical resistance', exact=True).input_value(), '#aa1122')
                page.evaluate('window.v6Root.unmount()')
            finally:
                browser.close()
