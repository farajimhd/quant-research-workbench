"""Real-shard browser acceptance and theme/scale captures; no API fixtures."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();out=Path(args.output_dir).resolve()
    if not out.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime output required')
    out.mkdir(parents=True,exist_ok=True)
    errors=[];captures=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            page=browser.new_page(viewport=dict(width=1600,height=1000))
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:5173/?research=bar-shards#research-workspace')
            page.get_by_role('heading',name='BarGPT shard inspection').wait_for()
            page.get_by_label('Ticker search',exact=True).fill('WING')
            page.get_by_label('Year',exact=True).select_option('2025')
            job='2025-133c35d54e3d25462114'
            page.get_by_label('Ticker / year',exact=True).locator(f'option[value="{job}"]').wait_for(state='attached',timeout=60000)
            page.get_by_label('Ticker / year',exact=True).select_option(job)
            checked=[]
            for frame,label in [(100000,'100ms'),(1000000,'1s'),(10000000,'10s'),(60000000,'1min'),(3600000000,'1hour'),(86400000000,'Daily')]:
                page.get_by_label('Timeframe',exact=True).select_option(str(frame))
                shard='coarse' if frame>=3600000000 else '2025-01-03'
                option=f'{shard}/samples-0000.npy'
                page.get_by_label('Published shard',exact=True).locator(f'option[value="{option}"]').wait_for(state='attached',timeout=90000)
                page.get_by_label('Published shard',exact=True).select_option(option)
                with page.expect_response(lambda r:'/bargpt/shards/sample?' in r.url,timeout=120000) as response:
                    page.get_by_role('button',name='Load shard',exact=True).click()
                assert response.value.ok,response.value.text()
                page.get_by_role('heading',name=f'WING · 2025 · {label}',exact=True).wait_for()
                assert page.locator('svg.bar-shard-chart g[role="button"]').count()==64
                page.get_by_role('button',name='Inspect bar 2',exact=True).click()
                page.get_by_role('heading',name='Bar 2 / 64',exact=True).wait_for()
                assert page.locator('.bar-shard-fields tbody tr').count()==21
                checked.append(label)
            # Exercise random selection within the current ticker/year/timeframe filters.
            with page.expect_response(lambda r:'/bargpt/shards/sample?' in r.url,timeout=120000) as response:
                page.get_by_role('button',name='Random sample',exact=True).click()
            assert response.value.ok,response.value.text()
            page.get_by_role('heading',name='WING · 2025 · Daily',exact=True).wait_for()
            # Capture the populated surface across app theme, scale and viewport dimensions.
            for theme in ('light','dark'):
                for scale in (.8,1.,1.25):
                    for name,width,height in [('normal',1600,1000),('compact',1000,760)]:
                        page.evaluate("([theme,scale])=>{localStorage.setItem('quant-research-workbench.theme',theme);localStorage.setItem('quant-research-workbench.ui-scale',String(scale));}",[theme,scale])
                        page.set_viewport_size(dict(width=width,height=height))
                        page.reload();page.get_by_role('heading',name='BarGPT shard inspection').wait_for()
                        # Route persistence is process-local; reload starts with the requested deep link.
                        page.get_by_label('Ticker search',exact=True).fill('WING')
                        page.get_by_label('Year',exact=True).select_option('2025')
                        page.get_by_label('Timeframe',exact=True).select_option('86400000000')
                        with page.expect_response(lambda r:'/bargpt/shards/sample?' in r.url,timeout=120000):
                            page.get_by_role('button',name='Random sample',exact=True).click()
                        page.get_by_role('heading',name='WING · 2025 · Daily',exact=True).wait_for()
                        page.wait_for_timeout(200)
                        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+2')
                        page.screenshot(path=str(out/f'{theme}-{scale}-{name}.png'),full_page=True)
                        captures.append(dict(theme=theme,scale=scale,viewport=name))
            assert not errors,errors
            (out/'report.json').write_text(json.dumps(dict(passed=True,timeframes=checked,captures=captures,errors=errors),indent=2))
            print(json.dumps(dict(passed=True,timeframes=checked,captures=len(captures),errors=errors)))
        finally:browser.close()


if __name__=='__main__':main()
