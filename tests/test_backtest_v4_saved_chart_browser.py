"""Opt-in real-browser regression for SELECT-only saved chart overlays."""
from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(
    not os.environ.get("CHART_BROWSER_TEST_URL") or not os.environ.get("SAVED_V4_CHART_RUN"),
    reason="Managed chart URL and completed Strategy 1 run are required",
)
def test_indicator_selection_keeps_candles_and_viewport():
    from playwright.sync_api import sync_playwright

    run_id = os.environ["SAVED_V4_CHART_RUN"]
    ticker = os.environ.get("SAVED_V4_CHART_TICKER", "SLE")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            requests: list[str] = []
            errors: list[str] = []
            page.on("request", lambda request: requests.append(request.url)
                    if "/v4-chart" in request.url else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(os.environ["CHART_BROWSER_TEST_URL"])
            page.evaluate("""async ({runId, ticker}) => {
              const React = (await import('/node_modules/.vite/deps/react.js')).default;
              const dom = await import('/node_modules/.vite/deps/react-dom_client.js');
              const {BacktestV4SavedChart} = await import('/src/app/components/BacktestV4SavedChart.tsx');
              document.getElementById('root').style.display = 'none';
              const node = document.createElement('div');
              document.body.appendChild(node);
              node.style.height = '850px';
              (dom.default ?? dom).createRoot(node).render(
                React.createElement(BacktestV4SavedChart, {runId, ticker}));
              window.savedChartViewport = () => {
                const shell = document.querySelector('.backtest-v4-saved-chart .chart-shell');
                let fiber = shell?.[Object.keys(shell).find(key => key.startsWith('__reactFiber'))];
                let chart, candle;
                while (fiber) {
                  let hook = fiber.memoizedState;
                  while (hook) {
                    const value = hook.memoizedState?.current;
                    if (value?.timeScale && value?.panes) chart = value;
                    if (value?.seriesType?.() === 'Candlestick') candle = value;
                    hook = hook.next;
                  }
                  fiber = fiber.return;
                }
                window.savedChartApi = () => chart;
                return {logical: chart?.timeScale().getVisibleLogicalRange(),
                  time: chart?.timeScale().getVisibleRange(),
                  price: candle?.priceScale().getVisibleRange(),
                  priceHeight: chart?.panes()[0].getHeight()};
              };
            }""", {"runId": run_id, "ticker": ticker})
            page.locator(".backtest-v4-quote").wait_for(timeout=30_000)
            baseline = page.evaluate("savedChartViewport()")
            bar_reads = sum("/v4-chart?" in url for url in requests)
            assert bar_reads == 1
            page.locator("button.chart-column-select-button").last.click()
            with page.expect_response(lambda response: "/v4-chart-overlays" in response.url,
                                      timeout=30_000):
                page.get_by_text("EMA 7", exact=True).last.click()
            page.wait_for_function("""() => localStorage.getItem(
              'backtest-v4-saved-chart.indicators-v1')?.includes('saved.ema_7')""")
            assert sum("/v4-chart?" in url for url in requests) == bar_reads
            assert sum("/v4-chart-overlays" in url for url in requests) == 1
            page.get_by_text("RSI 14", exact=True).last.click()
            page.wait_for_timeout(800)
            assert page.evaluate("savedChartViewport()") == baseline
            page.get_by_text("RSI 14", exact=True).last.click()
            page.wait_for_timeout(800)
            assert page.evaluate("savedChartViewport()") == baseline
            assert page.locator(".session-region").evaluate(
                "node => getComputedStyle(node.parentElement).bottom") == "0px"
            page.evaluate("savedChartViewport()")
            with page.expect_response(lambda response: "/v4-chart?" in response.url,
                                      timeout=30_000):
                page.evaluate("savedChartApi().timeScale().setVisibleLogicalRange({from:0,to:180})")
            left_view = page.evaluate("savedChartViewport()")
            page.wait_for_function("""() => savedChartViewport().logical?.from >= 1000""")
            reloaded_view = page.evaluate("savedChartViewport()")
            assert reloaded_view["time"] == left_view["time"]
            assert reloaded_view["price"] == left_view["price"]
            assert reloaded_view["priceHeight"] == left_view["priceHeight"]
            assert sum("/v4-chart?" in url for url in requests) == bar_reads + 1
            assert not errors
        finally:
            browser.close()


@pytest.mark.skipif(not os.environ.get("CHART_BROWSER_TEST_URL"),
                    reason="Managed chart URL is required")
def test_position_slider_explicitly_focuses_its_selected_trade():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(os.environ["CHART_BROWSER_TEST_URL"])
            page.evaluate("""async () => {
              const React = (await import('/node_modules/.vite/deps/react.js')).default;
              const dom = await import('/node_modules/.vite/deps/react-dom_client.js');
              const {ChartPanel} = await import('/src/app/components/ChartPanel.tsx');
              const root = document.createElement('div'); document.body.appendChild(root);
              document.getElementById('root').style.display = 'none';
              root.style.height = '760px';
              const origin = Date.parse('2026-08-18T13:00:00Z') / 1000;
              const candles = Array.from({length: 240}, (_, index) => ({
                time: origin + index, open: 4 + index / 1000,
                high: 4.1 + index / 1000, low: 3.9 + index / 1000,
                close: 4.01 + index / 1000}));
              const trade = (id, start, reason) => ({
                id, color: '#0088ff', entryTime: origin + start,
                entryPrice: 4 + start / 1000, status: 'closed',
                exitTime: origin + start + 8, exitPrice: 4.01 + start / 1000,
                stopPrice: id === 'first' ? 3.3 : 3.6,
                targetPrices: [id === 'first' ? 4.8 : 5.2],
                positionSide: 'LONG', exitFills: [{kind:'exit_fill',
                  time: origin + start + 8, price: 4.01 + start / 1000,
                  side:'SELL', labelParts:[{text:reason,tone:'reason'}]}],
              });
              const old = CanvasRenderingContext2D.prototype.fillText;
              window.__paintedTradeLabels = [];
              CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
                window.__paintedTradeLabels.push(String(text));
                return old.call(this, text, ...args);
              };
              (dom.default ?? dom).createRoot(root).render(React.createElement(ChartPanel, {
                ticker:'SLE', timeframe:'1s', timeframes:['1s'], baseHeight:640,
                settingsStorageKey:'slider-focus-regression', persistedOnly:true,
                strategyPresentationEnabled:true, visibleColumns:[], featureOptions:[],
                indicatorOptions:[], displayItemOptions:[],
                payload:{candles, volume:[], overlay_series:[], oscillator_series:[],
                  markers:[], regions:[], trade_annotations:[
                    trade('first', 25, 'Stop hit'), trade('second', 190, 'Target hit')]},
              }));
              window.focusPriceRange = () => {
                const shell = document.querySelector('.chart-shell');
                let fiber = shell?.[Object.keys(shell).find(key => key.startsWith('__reactFiber'))];
                while (fiber) {
                  let hook = fiber.memoizedState;
                  while (hook) {
                    const value = hook.memoizedState?.current;
                    if (value?.seriesType?.() === 'Candlestick') return value.priceScale().getVisibleRange();
                    hook = hook.next;
                  }
                  fiber = fiber.return;
                }
              };
            }""")
            slider = page.get_by_role("slider", name="Strategy position")
            slider.wait_for(timeout=10_000)
            assert slider.input_value() == "1"
            page.evaluate("window.__paintedTradeLabels = []")
            slider.press("ArrowRight")
            page.wait_for_function("""() => document.querySelector(
              '[aria-label="Strategy position"]')?.value === '2'""")
            page.wait_for_function("""() => window.__paintedTradeLabels?.includes('Target hit')""")
            page.wait_for_function("""() => { const range = window.focusPriceRange();
              return range && range.from < 3.6 && range.to > 5.2 && range.from > 3.3 && range.to < 5.5; }""")
            assert page.evaluate("window.__paintedTradeLabels.some(t => t.startsWith('09:03:'))")
            page.evaluate("window.__paintedTradeLabels = []")
            slider.press("ArrowLeft")
            page.wait_for_function("""() => document.querySelector(
              '[aria-label="Strategy position"]')?.value === '1'""")
            page.wait_for_function("""() => window.__paintedTradeLabels?.includes('Stop hit')""")
            assert page.evaluate("window.__paintedTradeLabels.some(t => t.startsWith('09:00:'))")
            assert not errors
        finally:
            browser.close()


@pytest.mark.skipif(
    not os.environ.get("CHART_BROWSER_TEST_URL") or not os.environ.get("SAVED_V4_CHART_RUN"),
    reason="Managed chart URL and completed Strategy 1 run are required",
)
def test_v7_presentation_controls_update_cached_causal_levels_without_bar_reads():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            requests: list[str] = []
            page.on("request", lambda request: requests.append(request.url)
                    if "/v4-chart" in request.url else None)
            page.goto(os.environ["CHART_BROWSER_TEST_URL"])
            page.evaluate("""async ({runId, ticker}) => {
              const React = (await import('/node_modules/.vite/deps/react.js')).default;
              const dom = await import('/node_modules/.vite/deps/react-dom_client.js');
              const {BacktestV4SavedChart} = await import('/src/app/components/BacktestV4SavedChart.tsx');
              document.getElementById('root').style.display = 'none';
              const node = document.createElement('div'); document.body.appendChild(node);
              node.style.height = '850px';
              (dom.default ?? dom).createRoot(node).render(
                React.createElement(BacktestV4SavedChart, {runId, ticker}));
              window.v7Zones = () => {
                const shell = document.querySelector('.backtest-v4-saved-chart .chart-shell');
                let fiber = shell?.[Object.keys(shell).find(key => key.startsWith('__reactFiber'))];
                while (fiber) {
                  if (fiber.memoizedProps?.payload?.price_zones) return fiber.memoizedProps.payload.price_zones;
                  fiber = fiber.return;
                }
                return [];
              };
            }""", {"runId": os.environ["SAVED_V4_CHART_RUN"],
                    "ticker": os.environ.get("SAVED_V4_CHART_TICKER", "SLE")})
            page.locator(".backtest-v4-quote").wait_for(timeout=30_000)
            page.locator("button.chart-column-select-button").last.click()
            with page.expect_response(lambda response: "/v4-chart-overlays" in response.url,
                                      timeout=30_000):
                page.get_by_text("V7 structural bands · provisional", exact=True).last.click()
            page.wait_for_function("() => v7Zones().length > 0")
            bar_reads = sum("/v4-chart?" in url for url in requests)
            page.locator(".chart-legend-header").first.click()
            page.get_by_role("button", name="Configure V7 structural bands · provisional").click()
            assert page.locator(".saved-v7-editor input[type=color]").count() == 6
            page.get_by_label("resistance band opacity").fill("70")
            page.wait_for_function("""() => v7Zones().some(zone =>
              zone.label === 'V7 R' && zone.savedBandOpacity === 0.7)""")
            page.get_by_label("Show resistance line").uncheck()
            page.wait_for_function("""() => JSON.parse(localStorage.getItem(
              'backtest-v4-saved-chart.v7-style-v1')).roles.resistance.line.visible === false""")
            page.get_by_label("V7 source").select_option("historical")
            page.wait_for_function("""() => JSON.parse(localStorage.getItem(
              'backtest-v4-saved-chart.v7-style-v1')).source === 'historical'""")
            assert sum("/v4-chart?" in url for url in requests) == bar_reads
            assert sum("/v4-chart-overlays" in url for url in requests) == 1
            assert '"source":"historical"' in page.evaluate(
                "localStorage.getItem('backtest-v4-saved-chart.v7-style-v1')")
        finally:
            browser.close()
