import asyncio
import os
from pathlib import Path

import pytest

from tests.test_backtest_performance_extrema import performance_scenario


@pytest.mark.skipif(not os.environ.get('PERFORMANCE_REVIEW_OUTPUT'),reason='Managed browser review is opt-in')
def test_performance_panel_open_closed_and_lifecycle():
    from playwright.sync_api import sync_playwright
    opened,closed=asyncio.run(performance_scenario())
    output=Path(os.environ['PERFORMANCE_REVIEW_OUTPUT']);output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:5173')
            page.evaluate('''async()=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {TradingJournalPreview}=await import('/src/features/canvas/tradingPresentation.tsx');
              document.getElementById('root').style.display='none';
              const node=document.createElement('div');document.body.appendChild(node);
              node.style.cssText='zoom:var(--app-zoom);width:calc(100vw / var(--app-zoom));padding:16px;box-sizing:border-box';
              const root=(dom.default??dom).createRoot(node);
              window.renderPerformance=data=>root.render(React.createElement(TradingJournalPreview,{data,settings:{limit:100,showRiskMultiple:true}}));
            }''')
            page.evaluate('data=>window.renderPerformance(data)',opened)
            page.get_by_role('button',name='View lifecycle').wait_for()
            page.get_by_role('button',name='View lifecycle').click()
            page.get_by_role('dialog').wait_for()
            page.screenshot(path=str(output/'lifecycle.png'))
            page.keyboard.press('Escape')
            # A live modal remains linked to the same lifecycle as it closes.
            page.evaluate('data=>window.renderPerformance(data)',closed)
            page.wait_for_timeout(300)
            if page.get_by_role('dialog').count():
                page.get_by_role('dialog').get_by_role('button',name='Close',exact=True).click()
            assert page.get_by_text('No open positions. Completed lifecycles remain in Positions.').is_visible()
            for label,value in [('Peak unrealized','$100.00'),('Worst unrealized','-$20.00'),
                ('Winning-trade profit','$50.00'),('Losing-trade loss','$20.00'),('Max drawdown','$120.00')]:
                metric=page.locator('.journal-metric').filter(has=page.locator('span',has_text=label))
                assert value in metric.inner_text()
            for theme,scale,width in [('light',1,1440),('dark',.8,1000),('light',1.25,1000)]:
                page.set_viewport_size({'width':width,'height':1100})
                page.evaluate('''async([theme,s])=>{const d=document.documentElement;
                  (await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);
                  d.style.setProperty('--app-zoom-inverse',1/s)}''',[theme,scale])
                for state,data in [('open',opened),('closed',closed)]:
                    page.evaluate('data=>window.renderPerformance(data)',data)
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(output/f'{state}-{theme}-{scale}.png'),full_page=True)
            assert not errors
        finally:browser.close()


@pytest.mark.skipif(not os.environ.get('JOURNAL_CHART_REVIEW_OUTPUT'), reason='Managed browser review is opt-in')
def test_journal_charts_attach_after_empty_state_and_resize_with_time_axes():
    import json
    from urllib.request import urlopen
    from playwright.sync_api import sync_playwright
    run_id = os.environ['BACKTEST_RECOVERY_RUN_ID']
    with urlopen(f'http://127.0.0.1:8000/api/trading/backtest/runs/{run_id}/canvas?symbol=AAPL&lazy=true&include_chart=false') as response:
        data = json.load(response)['trading']
    assert len(data['performance_journal']['equity_curve']) > 2
    output = Path(os.environ['JOURNAL_CHART_REVIEW_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':1600,'height':1000})
            page.goto('http://127.0.0.1:5173')
            page.evaluate("""async()=>{
              const React=(await import('/node_modules/.vite/deps/react.js')).default;
              const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
              const {TradingJournalPreview}=await import('/src/features/canvas/tradingPresentation.tsx');
              document.getElementById('root').style.display='none';
              const node=document.createElement('div');document.body.appendChild(node);
              node.style.cssText='zoom:var(--app-zoom);width:calc(100vw / var(--app-zoom));padding:16px;box-sizing:border-box';
              const root=(dom.default??dom).createRoot(node);
              window.renderPerformance=data=>root.render(React.createElement(TradingJournalPreview,{data,settings:{limit:100,showRiskMultiple:true}}));
            }""")
            page.evaluate('()=>window.renderPerformance(undefined)')
            page.get_by_text('Close at least one flat-to-flat episode to build the performance curve').wait_for()
            page.evaluate('data=>window.renderPerformance(data)', data)
            page.locator('.journal-area-chart').wait_for()
            for theme in ('light','dark'):
                for scale in (.8,1,1.25):
                    for viewport,width in [('normal',1600),('compact',900)]:
                        page.set_viewport_size({'width':width,'height':1000})
                        page.evaluate("""async([theme,s])=>{const d=document.documentElement;
                          (await import('/src/app/theme.ts')).applyThemeDefinition(d,theme);d.style.setProperty('--app-zoom',s);
                          d.style.setProperty('--app-zoom-inverse',1/s)}""",[theme,scale])
                        page.wait_for_function("""()=>[...document.querySelectorAll('.journal-area-chart,.journal-candle-chart')].every(el=>Math.abs(el.viewBox.baseVal.width-el.clientWidth)<2)""")
                        labels=page.locator('.journal-area-chart .journal-time-axis text').all_text_contents()
                        assert len(labels)>=2 and all(':' in label for label in labels), labels
                        for selector in ('.journal-area-chart','.journal-candle-chart'):
                            first=page.locator(selector).get_attribute('viewBox')
                            page.evaluate('data=>window.renderPerformance(data)',data)
                            page.wait_for_timeout(100)
                            assert page.locator(selector).get_attribute('viewBox')==first
                        page.screenshot(path=str(output/f'overview-{theme}-{scale}-{viewport}.png'),full_page=True)
            # Irregular episode intervals must occupy proportional elapsed time.
            points=page.locator('.journal-area-chart polyline').get_attribute('points').split()
            xs=[float(point.split(',')[0]) for point in points]
            from datetime import datetime
            times=[datetime.fromisoformat(row['time']).timestamp() for row in data['performance_journal']['equity_curve']]
            for x,at in zip(xs,times):
                assert abs((x-xs[0])/(xs[-1]-xs[0])-(at-times[0])/(times[-1]-times[0])) < 1e-6
        finally:
            browser.close()
