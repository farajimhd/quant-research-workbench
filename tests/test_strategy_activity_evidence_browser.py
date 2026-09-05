"""Opt-in browser regression for interpreting recorded strategy gates."""
import json
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get("CHART_BROWSER_TEST_URL"), "Managed frontend URL required")
class StrategyEvidenceBrowserTests(unittest.TestCase):
    def test_entry_reference_buffer_and_actual_distance_are_distinct(self):
        from playwright.sync_api import sync_playwright

        condition = dict(condition_id="squeeze-price-over-unified-resistance",
                         left_source_id="data.market.last_price@1:value", left_value=6.6646,
                         right_source_id="data.indicator.structure.unified_resistance_upper@1:value",
                         right_value=6.6825, threshold_value=6.6825, buffer_bps=0,
                         comparator="above_by_bps", passed=False)
        row = dict(record_id="evidence-regression", sequence=1, event_time="2026-08-21T11:16:11Z",
                   ticker="JUNS", event_type="decision", action="wait", state="watching",
                   reason="Waiting for completed close above current R3", strategy_revision=47,
                   gate_snapshot=dict(entry_rules=dict(trigger=dict(passed=False, condition_evidence=[condition])),
                                      unified_structural_trigger=dict(reason="waiting_for_completed_close_above_current_r3",
                                          current_snapshot=dict(session_high=7.3, levels=[dict(price=p) for p in [7.29, 7.2, 6.6825]]))))
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=dict(width=1600, height=1000))
                page.route("**/api/trading/strategy-activity?**", lambda route: route.fulfill(
                    content_type="application/json", body=json.dumps(dict(rows=[row], complete=True))))
                page.goto(os.environ["CHART_BROWSER_TEST_URL"])
                page.evaluate("""async row => {
                    const React=(await import('/node_modules/.vite/deps/react.js')).default;
                    const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
                    const {StrategyActivityContainer}=await import('/src/app/components/MarketScreenerContainers.tsx');
                    document.getElementById('root').style.display='none';
                    const host=document.createElement('div'); document.body.appendChild(host);
                    host.style.cssText='height:calc(var(--app-zoomed-viewport-height) - 32px);padding:16px;zoom:var(--app-zoom)';
                    (dom.default??dom).createRoot(host).render(React.createElement(StrategyActivityContainer,{
                        asOf:row.event_time,historicalRows:[row],historicalPage:{complete:true},
                        settings:{eventType:'',limit:2000,runId:'',strategyId:'',ticker:''},
                        onSettingsChange:()=>{},onTickerSelect:()=>{}}));
                }""", row)
                page.get_by_role("button", name="Inspect strategy event", exact=False).click()
                gate = page.locator('.strategy-activity-evidence-card').filter(has=page.get_by_text("Trigger gate", exact=True))
                gate.get_by_text("Entry resistance selection", exact=True).wait_for()
                text = gate.inner_text()
                for expected in ["R3: $6.6825", "Last traded price", "$0.0179 below threshold", "No extra buffer", "strictly above $6.6825"]:
                    self.assertIn(expected, text)
                self.assertNotIn("Data.Market.Last", text)
                for theme in ["light", "dark"]:
                    for scale in [.8, 1, 1.25]:
                        for width, height in [(1600, 1000), (1280, 720)]:
                            page.set_viewport_size(dict(width=width, height=height))
                            page.evaluate("""async ({theme,scale}) => {
                                const {applyThemeDefinition}=await import('/src/app/theme.ts');
                                applyThemeDefinition(document.documentElement,theme);
                                const style=document.documentElement.style;
                                style.setProperty('--app-zoom',String(scale));
                                style.setProperty('--app-zoom-inverse',String(1/scale));
                                style.setProperty('--app-zoomed-viewport-height',`${100/scale}vh`);
                                style.setProperty('--app-zoomed-viewport-width',`${100/scale}vw`);
                                style.setProperty('--app-readable-scale',String(scale<1?1/scale:1));
                                style.setProperty('--app-overlay-scale',String(Math.max(1,scale)));
                            }""", dict(theme=theme, scale=scale))
                            gate.scroll_into_view_if_needed()
                            self.assertTrue(gate.is_visible())
                            output = os.environ.get("STRATEGY_EVIDENCE_REVIEW_DIR")
                            if output:
                                Path(output).mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(Path(output) / f"{theme}-{scale}-{width}.png"))
            finally:
                browser.close()
