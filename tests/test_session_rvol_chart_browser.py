"""Exercise the optional canonical RVOL series against managed Vite."""
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get("CHART_BROWSER_TEST_URL"), "Managed frontend URL required")
class SessionRvolChartBrowserTests(unittest.TestCase):
    def test_optional_series_preserves_canonical_values_and_missing_gaps(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(os.environ["CHART_BROWSER_TEST_URL"])
                page.evaluate("""async () => {
                  const React = (await import('/node_modules/.vite/deps/react.js')).default;
                  const dom = await import('/node_modules/.vite/deps/react-dom_client.js');
                  const {ChartPanel} = await import('/src/app/components/ChartPanel.tsx');
                  const {historicalIndicatorSeries} = await import('/src/features/canvas/chartPresentation.tsx');
                  const {CHART_INDICATORS} = await import('/src/features/canvas/configuration.ts');
                  const {MAIN_CHART_DEFAULT_INDICATORS} = await import('/src/features/canvas/chartDefaults.ts');
                  const id = 'indicator.session_relative_volume';
                  const item = CHART_INDICATORS.find(item => item.id === id);
                  if (!item || item.title !== 'Session RVOL (20 days)' || item.sourceColumns.join() !== 'session_relative_volume') throw Error('Missing selector contract');
                  if (MAIN_CHART_DEFAULT_INDICATORS.includes(id)) throw Error('RVOL must be optional');
                  const values = [null, 0, 0.5, 1, 2.25, undefined, 3];
                  const rows = values.map((value, i) => ({bar_start:new Date((1787301300+i)*1000).toISOString(), session_relative_volume:value}));
                  if (historicalIndicatorSeries(rows, 'oscillator', []).length) throw Error('Unselected oscillator rendered');
                  const series = historicalIndicatorSeries(rows, 'oscillator', [id]);
                  if (series.length !== 1 || series[0].paneKey !== 'session_relative_volume' || series[0].axisTitle !== 'RVOL (x)' || series[0].priceScaleId !== 'right') throw Error('Incorrect pane or detached scale');
                  values.forEach((value,i) => { if(value == null ? !Number.isNaN(series[0].data[i].value) : series[0].data[i].value !== value) throw Error('Canonical value or gap changed'); });
                  document.getElementById('root').style.display='none';
                  const node=document.createElement('div'); document.body.appendChild(node);
                  const root=(dom.default??dom).createRoot(node);
                  window.paintSessionRvol = (unavailable = false, selected = true) => root.render(React.createElement(ChartPanel, {
                    ticker:'BMNR',timeframe:'1s',timeframes:['1s'],baseHeight:600,settingsStorageKey:'session-rvol-test',
                    dataStatus: unavailable ? 'Session RVOL unavailable' : undefined,
                    visibleColumns:selected ? [id] : [],featureOptions:[],indicatorOptions:[],displayItemOptions:CHART_INDICATORS,
                    payload:{candles:values.map((_,i)=>({time:1787301300+i,open:10,close:10.1,high:10.2,low:9.9})),volume:[],overlay_series:[],oscillator_series: !selected ? [] : unavailable ? historicalIndicatorSeries(rows.map(row=>({...row,session_relative_volume:null})), 'oscillator', [id]) : series,markers:[],regions:[]}
                  }));
                  window.paintSessionRvol(false, false);
                }""")
                page.wait_for_timeout(500)
                page.evaluate("""() => {
                  const seen = new Set();
                  for (const el of document.querySelectorAll('div')) {
                    let fiber = el[Object.keys(el).find(k => k.startsWith('__reactFiber'))];
                    while (fiber && !seen.has(fiber)) {
                      seen.add(fiber);
                      for (let hook = fiber.memoizedState, n = 0; hook && n++ < 300; hook = hook.next) {
                        const value = hook.memoizedState?.current;
                        if (value && typeof value.panes === 'function' && typeof value.timeScale === 'function') window.rvolTestChart = value;
                      }
                      fiber = fiber.return;
                    }
                  }
                  if (!window.rvolTestChart) throw Error('Chart runtime not found');
                  window.rvolTestChart.priceScale('right', 0).setVisibleRange({from:9.8,to:10.4});
                  window.paintSessionRvol();
                }""")
                page.wait_for_timeout(500)
                page.evaluate("""() => {
                  const pane = window.rvolTestChart.panes()[1];
                  const series = pane.getSeries().find(s => s.options().title === 'RVOL (x)');
                  if (!series || !series.priceScale().options().autoScale) throw Error('New oscillator inherited locked candle scale');
                  for (const value of [0, 0.5, 1, 2.25, 3]) {
                    const y = series.priceToCoordinate(value);
                    if (y === null || y < 0 || y > pane.getHeight()) throw Error('RVOL value clipped outside pane');
                  }
                }""")
                self.assertGreater(page.locator("canvas").count(), 1)
                output = os.environ.get("SESSION_RVOL_REVIEW_OUTPUT")
                if output:
                    target = Path(output)
                    target.mkdir(parents=True, exist_ok=True)
                    for theme, scale, width in [("light", 1, 1500), ("dark", 0.8, 900), ("light", 1.25, 900)]:
                        page.set_viewport_size({"width": width, "height": 950})
                        page.evaluate("""async ([theme, scale]) => {
                          const {applyThemeDefinition} = await import('/src/app/theme.ts');
                          applyThemeDefinition(document.documentElement, theme);
                          document.documentElement.style.setProperty('--app-zoom', String(scale));
                          document.documentElement.style.setProperty('--app-zoom-inverse', String(1/scale));
                          document.documentElement.style.setProperty('--app-overlay-scale', String(Math.max(scale, 1)));
                          window.dispatchEvent(new Event('resize'));
                        }""", [theme, scale])
                        page.wait_for_timeout(200)
                        page.screenshot(path=str(target / f"session-rvol-{theme}-{scale}-{width}.png"))
                page.evaluate("window.paintSessionRvol(true)")
                page.get_by_text("Session RVOL unavailable", exact=True).wait_for()
                if output:
                    page.screenshot(path=str(target / "session-rvol-unavailable.png"))
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
