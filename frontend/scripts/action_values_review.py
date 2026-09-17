"""Real endpoint, chart and keyboard checks for the hindsight action spectrum."""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def mount_action_chart(page,path,ticker,session_date):
    source=json.loads(Path(path).read_text())
    assert not source['has_more'] and source['indicator_provenance']['complete']
    rows=[dict(time=datetime.fromisoformat(b['bar_start'].replace('Z','+00:00')).timestamp(),
               endTime=datetime.fromisoformat(b['bar_end'].replace('Z','+00:00')).timestamp(),
               **{k:b[k] for k in ('open','high','low','close','volume')}) for b in source['bars']]
    page.evaluate("""async ({rows,ticker,date,asOf}) => {
      const {default:React}=await import('/node_modules/.vite/deps/react.js');
      const {default:ReactDOM}=await import('/node_modules/.vite/deps/react-dom_client.js');
      const {ChartPanel}=await import('/src/app/components/ChartPanel.tsx');
      const actionUrl=performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/src/app/components/HindsightActions.tsx')).at(-1);
      if(!actionUrl)throw new Error('Loaded action module not found');
      const {HindsightActionsPrimitive}=await import(actionUrl);
      const attached=HindsightActionsPrimitive.prototype.attached;
      HindsightActionsPrimitive.prototype.attached=function(args){attached.call(this,args);if(args.chart.chartElement().closest('#action-value-review')){window.actionReviewPrimitive=this;args.chart.subscribeClick(e=>window.actionReviewPoint=e.point);}};
      const host=document.createElement('div');host.id='action-value-review';host.className='app-shell';
      host.style.cssText='position:fixed;inset:0;z-index:100;background:var(--card);width:var(--app-zoomed-viewport-width);height:var(--app-zoomed-viewport-height);';
      document.getElementById('root').style.display='none';document.body.append(host);
      const payload={candles:rows,markers:[],overlay_series:[],oscillator_series:[],price_zones:[],regions:[],volume:[]};
      ReactDOM.createRoot(host).render(React.createElement(ChartPanel,{payload,ticker,timeframe:'1s',timeframes:['1s'],
        hindsightSessionDate:date,indicatorAsOf:asOf,settingsStorageKey:'review.action-values',
        indicatorOptions:[],featureOptions:[],visibleColumns:[],fillHeight:true,enableFullscreen:false,
        onTickerChange:()=>{},onTimeframeChange:()=>{},onVisibleColumnsChange:()=>{}}));
    }""",dict(rows=rows,ticker=ticker,date=session_date,asOf=source['as_of']))


def review_action_values(page, screenshot_path):
    chart=page.locator('#action-value-review') if page.locator('#action-value-review').count() else page
    toggle=chart.get_by_role('button',name='Hindsight action values',exact=True)
    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/research/hindsight-actions')) as response:
        toggle.click(timeout=30000)
    job=response.value.json()
    job=job.get('data',job)
    details=chart.get_by_role('button',name='Action value details',exact=True)
    details.click()
    try:
        page.wait_for_function("""() => document.querySelector('.action-values-table') ||
            document.querySelector('.action-values-error')""",timeout=45000)
    except Exception as exc:
        page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__action-failure.png')),full_page=True)
        raise RuntimeError(f'Action inspector failed: {page.locator(".action-values-controls").all_text_contents()}; initial response: {job}') from exc
    if page.locator('.action-values-error').count():raise RuntimeError(page.locator('.action-values-status').inner_text())
    url=urlsplit(page.url)
    response=page.request.get(f'{url.scheme}://{url.netloc}/api/research/hindsight-actions/{job["id"]}').json()
    result=response.get('data',response)['result']
    assert result['hindsight_only'] and result['counts']['seconds']==1801, 'Unexpected label window'
    assert result['path'][-1]['after']==0, 'Terminal inventory is not flat'
    move=max(result['moves'],key=lambda x:abs(x['net_cash']))
    page.get_by_role('combobox',name='Inspect hindsight move').select_option(str(move['number']))
    slider=page.get_by_role('slider',name='Action decision second')
    before=int(slider.input_value());slider.focus();page.keyboard.press('ArrowRight')
    assert int(slider.input_value())==before+1, 'Keyboard did not advance decision time'
    page.keyboard.press('ArrowLeft')
    inventory=page.get_by_role('combobox',name='Action starting position')
    inventory.select_option('0')
    assert 'Exit short' in page.locator('.action-values-table').inner_text(), 'Position selection did not update actions'
    assert result['position_size']==1 and result['inventory']==[-1,0,1]
    runs=result['action_runs']
    assert sum(r['end_index']-r['start_index']+1 for r in runs)==len(result['path'])
    assert all(a['action']!=b['action'] for a,b in zip(runs,runs[1:]))
    assert not page.get_by_role('spinbutton',name='Shares per adjustment',exact=True).count()
    inventory.select_option(str(result['path'][before]['state_index']))
    page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__action-details.png')),full_page=True)
    page.get_by_role('button',name='Center this second on chart').click()
    page.mouse.move(0,0);page.wait_for_timeout(350)
    pane=chart.locator('.chart-pane-canvas').first
    shown=pane.screenshot()
    page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__action-lines.png')),full_page=True)
    toggle.click();page.mouse.move(0,0);page.wait_for_timeout(250)
    hidden=pane.screenshot()
    assert shown!=hidden,'Action values did not paint on the price chart'
    toggle.click();page.mouse.move(0,0);page.wait_for_timeout(250)
    assert pane.screenshot()==shown,'Action toggle altered the chart viewport or underlying rendering'
    if page.locator('#action-value-review').count():
        assert page.evaluate('''() => {
          const p=window.actionReviewPrimitive;
          return p.hits.every(h=>{
            const run=p.result.action_runs.find(r=>h.index>=r.start_index && h.index<=r.end_index);
            return run && Math.abs(h.y-p.series.priceToCoordinate(run.price))<0.001;
          });
        }'''), 'Action line height changed within a run'
        hit=page.evaluate('''() => { const p=window.actionReviewPrimitive; return p.hits.find(h => h.x1>30 && h.x2>h.x1 && h.y>30 && !p.labelHits.some(l=>Math.abs(l.y-h.y)<15 && (h.x1+h.x2)/2>=l.x1 && (h.x1+h.x2)/2<=l.x2)); }''')
        assert hit, 'No clickable action segments'
        bounds=pane.bounding_box()
        zoom=pane.evaluate('(e) => e.getBoundingClientRect().width/e.offsetWidth')
        left=page.evaluate('() => window.actionReviewPrimitive.chart.priceScale("left").width()')
        page.mouse.click(bounds['x']+(left+(hit['x1']+hit['x2'])/2)*zoom,bounds['y']+hit['y']*zoom)
        try:page.get_by_role('dialog',name='Hindsight action values',exact=True).wait_for(timeout=5000)
        except Exception as exc:
            observed=page.evaluate('() => ({point:window.actionReviewPoint, hits:window.actionReviewPrimitive.hits.slice(0,3)})')
            raise RuntimeError(f'Line click failed: {hit=}, {zoom=}, {left=}, {observed=}') from exc
        assert int(page.get_by_role('slider',name='Action decision second').input_value())==hit['index'], 'Line click selected wrong second'
        page.keyboard.press('Escape')
        # A second native chart click inside its double-click interval is a zoom gesture.
        page.wait_for_timeout(600)
        label=page.evaluate('() => window.actionReviewPrimitive.labelHits.find(h=>h.x1>0 && h.y>15)')
        assert label, 'No clickable value labels'
        page.mouse.click(bounds['x']+(left+(label['x1']+label['x2'])/2)*zoom,bounds['y']+label['y']*zoom)
        dialog=page.get_by_role('dialog',name='Hindsight action values',exact=True)
        dialog.wait_for(timeout=5000)
        assert dialog.get_attribute('aria-modal')=='true'
        assert int(page.get_by_role('slider',name='Action decision second').input_value())==label['index']
        box=dialog.bounding_box();viewport=page.viewport_size
        assert abs(box['x']+box['width']/2-viewport['width']/2)<3, 'Modal not horizontally centered'
        assert abs(box['y']+box['height']/2-viewport['height']/2)<3, 'Modal not vertically centered'
        page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__label-modal.png')),full_page=True)
        page.keyboard.press('Escape')
    details.click();page.keyboard.press('Escape')
    assert not page.get_by_role('dialog',name='Hindsight action values',exact=True).count()
    assert details.evaluate('(e) => e === document.activeElement')
    return dict(seconds=result['counts']['seconds'],moves=len(result['moves']),adjustments=result['counts']['adjustments'],
                net_cash=result['net_cash'],paint_reversible=True,keyboard=True)
