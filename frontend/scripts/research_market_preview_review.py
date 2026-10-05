"""Real-data browser audit of the isolated 1b preview."""
import argparse,json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args()
 out=Path(args.output_dir).resolve()
 if not out.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('External runtime required')
 out.mkdir(parents=True,exist_ok=True);records=[];errors=[]
 with sync_playwright() as p:
  browser=p.chromium.launch()
  try:
   for theme in ['light','dark']:
    for scale in [.8,1.,1.25]:
     for size,viewport in [('normal',dict(width=1600,height=1000)),('compact',dict(width=1280,height=720))]:
      context=browser.new_context(viewport=viewport)
      context.add_init_script(f"localStorage.setItem('quant-research-workbench.theme','{theme}');localStorage.setItem('quant-research-workbench.ui-scale','{scale}');")
      page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
      charts=[];candidates=[];timelines=[]
      def proxy(route):
       u=urlsplit(route.request.url);response=route.fetch(url='http://127.0.0.1:8000'+u.path+('?' + u.query if u.query else ''),timeout=300000)
       if '/market-preview/chart' in u.path and response.ok:
        raw=response.json();charts.append(raw['data'] if response.headers.get('x-response-envelope')=='1' else raw)
       if '/market-preview/rows' in u.path and response.ok:
        raw=response.json();candidates.append(raw['data'] if response.headers.get('x-response-envelope')=='1' else raw)
       if '/market-preview/timeline' in u.path and response.ok:
        raw=response.json();timelines.append(raw['data'] if response.headers.get('x-response-envelope')=='1' else raw)
       route.fulfill(response=response)
      page.route('**/api/research/models**',proxy)
      page.goto('http://127.0.0.1:5173/#research-workspace');page.get_by_role('button',name='1b selection & sizing',exact=True).click()
      page.get_by_label('1b session',exact=True).select_option('2026-07-31')
      page.get_by_role('button',name='Prepare session preview',exact=True).click()
      page.get_by_text('3 · Inspect copied 1b labels',exact=True).wait_for(timeout=300000)
      scope=page.locator('.research-market-preview');name=f'{theme}-{scale}-{size}'
      bands=scope.get_by_role('group',name='Group timeline',exact=True)
      boxes=scope.get_by_role('group',name='Episode score boxes',exact=True)
      boxes.wait_for(timeout=300000)
      assert scope.get_by_text('Session selected score sum:',exact=False).count()==1
      geometry=boxes.evaluate("svg=>{const axis=document.querySelector('[aria-label=\"Group timeline\"]');const xs=Array.from(axis.querySelectorAll('line')).map(l=>+l.getAttribute('x1'));return {width:svg.width.baseVal.value,view:svg.viewBox.baseVal.width,axis:axis.viewBox.baseVal.width,gaps:xs.slice(1).map((x,i)=>x-xs[i])};}")
      assert geometry['width']==geometry['view']==geometry['axis']
      assert max(geometry['gaps'])-min(geometry['gaps'])<0.000001
      first_start=timelines[-1]['start_us']
      boxes.evaluate("svg=>window.__groupingSvg=svg")
      boxes.scroll_into_view_if_needed()
      bounds=boxes.bounding_box();assert bounds
      page.mouse.move(bounds['x']+bounds['width']*.7,bounds['y']+bounds['height']-5)
      page.mouse.down()
      page.mouse.move(bounds['x']+bounds['width']*.5,bounds['y']+bounds['height']-5,steps=8)
      with page.expect_response(lambda r:'/market-preview/timeline' in r.url,timeout=300000):page.mouse.up()
      boxes.wait_for(timeout=300000)
      assert timelines[-1]['start_us']>first_start
      assert boxes.evaluate("svg=>svg===window.__groupingSvg")
      with page.expect_response(lambda r:'/market-preview/timeline' in r.url,timeout=300000):scope.get_by_role('button',name='Zoom in',exact=True).click()
      boxes.wait_for(timeout=300000)
      assert timelines[-1]['end_us']-timelines[-1]['start_us']==150_000_000
      boxes.scroll_into_view_if_needed()
      bounds=boxes.bounding_box();assert bounds
      page.mouse.move(bounds['x']+bounds['width']*.5,bounds['y']+bounds['height']*.5)
      with page.expect_response(lambda r:'/market-preview/timeline' in r.url,timeout=300000):page.mouse.wheel(0,100)
      boxes.wait_for(timeout=300000)
      assert timelines[-1]['end_us']-timelines[-1]['start_us']==188_000_000
      with page.expect_response(lambda r:'/market-preview/timeline' in r.url,timeout=300000):scope.get_by_label('Grouping chart window',exact=True).select_option('300')
      boxes.wait_for(timeout=300000)
      with page.expect_response(lambda r:'/market-preview/timeline' in r.url,timeout=300000):scope.get_by_role('button',name='Session start',exact=True).click()
      boxes.wait_for(timeout=300000)
      assert timelines and all(r['entry_target_us']>r['time_us'] for r in timelines[-1]['rows'])
      with page.expect_response(lambda r:'/market-preview/result' in r.url,timeout=300000):
       bands.get_by_role('button').first.click()
      assert page.get_by_label('1b group',exact=True).input_value()
      page.get_by_label('1b group',exact=True).select_option('')
      with page.expect_response(lambda r:'/market-preview/chart' in r.url,timeout=300000):
       boxes.get_by_role('button').first.click()
      page.get_by_role('button',name='Next 1s close',exact=True).wait_for()
      page.wait_for_function("()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Next 1s close')?.disabled")
      with page.expect_response(lambda r:'/market-preview/rows' in r.url,timeout=300000):
       page.get_by_role('button',name='Next 1s close',exact=True).click()
      assert candidates and all(r['score']>0 for r in candidates[-1]['rows'])
      assert candidates[-1]['time_us']>candidates[0]['time_us']
      scope.locator(':scope > .research-preview-table button').first.click()
      scope.locator('.chart-shell').wait_for(timeout=300000)
      page.wait_for_timeout(500)
      assert scope.get_by_text('Chart renderer stopped',exact=True).count()==0
      assert charts and any(r['allocation_loss_mask'] for r in charts[-1]['labels'])
      page.screenshot(path=str(out/(name+'-selection.png')),full_page=True)
      scope.get_by_text('Exact 1a → 1b candle targets',exact=True).click()
      scope.get_by_role('checkbox',name='Show original 1a markers',exact=True).check()
      scope.get_by_role('checkbox',name='Show original 1a markers',exact=True).uncheck()
      page.get_by_label('1b selection',exact=True).select_option('rejected')
      with page.expect_response(lambda r:'/market-preview/result' in r.url and 'search=NVDA' in r.url,timeout=300000):
       page.get_by_label('1b find ticker',exact=True).fill('NVDA')
      page.wait_for_timeout(100)
      with page.expect_response(lambda r:'/market-preview/chart' in r.url,timeout=300000):
       scope.locator(':scope > .research-preview-table button').first.click()
      assert all(not r['allocation_loss_mask'] for r in charts[-1]['labels'] if r.get('group_id') is None)
      assert any(r['action_1a'] in ('ENTRY','HOLD','EXIT') and r['action']=='WAIT' for r in charts[-1]['labels'])
      page.wait_for_timeout(500)
      assert scope.get_by_text('Chart renderer stopped',exact=True).count()==0
      page.screenshot(path=str(out/(name+'-rejected.png')),full_page=True)
      records.append(dict(theme=theme,scale=scale,viewport=size,charts=len(charts)))
      context.close()
  finally:browser.close()
 if errors:raise AssertionError(errors)
 (out/'report.json').write_text(json.dumps(dict(status='passed',cases=records,page_errors=errors),indent=2));print(json.dumps(dict(status='passed',cases=len(records))))
if __name__=='__main__':main()
