"""Real endpoint, candle-click and keyboard checks for hindsight opportunities."""
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
    job=response.value.json();job=job.get('data',job)
    details=chart.get_by_role('button',name='Action value details',exact=True);details.click()
    page.wait_for_function("() => document.querySelector('.action-values-table') || document.querySelector('.action-values-error')",timeout=45000)
    if page.locator('.action-values-error').count():raise RuntimeError(page.locator('.action-values-status').inner_text())
    url=urlsplit(page.url)
    response=page.request.get(f'{url.scheme}://{url.netloc}/api/research/hindsight-actions/{job["id"]}').json()
    result=response.get('data',response)['result']
    assert result['horizon']=='next_base_macd_exit_per_direction' and result['position_size']==1
    assert result['counts']['seconds']==1801
    assert all(x['hold_seconds'] is None or x['hold_seconds']>=0 for x in result['labels'])
    slider=page.get_by_role('slider',name='Action decision second');slider.fill('595')
    slider.focus();page.keyboard.press('ArrowRight');assert slider.input_value()=='596';page.keyboard.press('ArrowLeft')
    table=page.locator('.action-values-inspector > .action-values-table')
    for name in ('Buy long','Open short','Stay flat'):assert table.get_by_role('cell',name=name,exact=True).count()==1
    assert '04:09:55.000' in page.locator('.action-values-inspector').inner_text()
    selected=result['labels'][595]
    for side in ('long','short'):
        value=selected[side]['gross_profit']
        expected=('+' if value>=0 else '-')+'$'+format(abs(value),'.4f').rstrip('0').rstrip('.')
        assert expected in table.inner_text()
    slider.fill('664')
    assert result['labels'][664]['short']['exit_time']==1787299868.067424
    assert '04:11:08.067' in table.inner_text()
    slider.fill('595')
    page.get_by_text('Actions for an existing position',exact=True).click()
    assert page.get_by_role('cell',name='Not calculated',exact=True).count()==4
    page.get_by_text('Actions for an existing position',exact=True).click()
    page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__action-details.png')),full_page=True)
    page.get_by_role('button',name='Center this second on chart').click();page.mouse.move(0,0);page.wait_for_timeout(500)
    pane=chart.locator('.chart-pane-canvas').first
    page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__clean-chart.png')),full_page=True)
    if page.locator('#action-value-review').count():
        candle=page.evaluate("""() => {
          const p=window.actionReviewPrimitive;
          if(p.paneViews || p.autoscaleInfo) throw new Error('Action drawings still attached');
          for(const c of p.candles){
            const index=p.result.labels.findIndex(r=>r.time===(c.endTime??c.time+1));
            const x=p.coordinate(c.time), y=p.series.priceToCoordinate((c.high+c.low)/2);
            if(c.isClosed!==false && index>=0 && x>30 && x<p.chart.timeScale().width()-30 && y>30 && y<p.chart.chartElement().clientHeight-40)
              return {x,y,index};
          }
        }""")
        assert candle
        bounds=pane.bounding_box();zoom=pane.evaluate('(e)=>e.getBoundingClientRect().width/e.offsetWidth')
        left=page.evaluate('()=>window.actionReviewPrimitive.chart.priceScale("left").width()')
        page.mouse.click(bounds['x']+(left+candle['x'])*zoom,bounds['y']+candle['y']*zoom)
        dialog=page.get_by_role('dialog',name='Hindsight action values',exact=True);dialog.wait_for(timeout=5000)
        assert int(page.get_by_role('slider',name='Action decision second').input_value())==candle['index']
        box=dialog.bounding_box();viewport=page.viewport_size
        assert dialog.get_attribute('aria-modal')=='true'
        assert abs(box['x']+box['width']/2-viewport['width']/2)<3
        assert abs(box['y']+box['height']/2-viewport['height']/2)<3
        page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__candle-modal.png')),full_page=True)
        page.keyboard.press('Escape')
    toggle.click();page.mouse.move(0,0);page.wait_for_timeout(300)
    if page.locator('#action-value-review').count():
        page.mouse.click(bounds['x']+(left+candle['x'])*zoom,bounds['y']+candle['y']*zoom)
        assert not page.get_by_role('dialog',name='Hindsight action values',exact=True).count()
    toggle.click();page.mouse.move(0,0);page.wait_for_timeout(300)
    details.click();page.keyboard.press('Escape')
    assert not page.get_by_role('dialog',name='Hindsight action values',exact=True).count()
    return dict(counts=result['counts'],horizon='macd',candle_click=True,modal=True)
