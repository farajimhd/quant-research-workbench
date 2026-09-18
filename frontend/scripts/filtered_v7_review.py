"""Deterministic review of the real preparation component and modal."""
def review_filtered_v7(page, screenshot_path):
    page.evaluate("""async () => {
      const {FilteredV7Preparation} = await import('/src/app/components/FilteredV7Preparation.tsx');
      const {Modal} = await import('/src/app/components/Modal.tsx');
      const {default: React} = await import('/node_modules/.vite/deps/react.js');
      const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
      await import('/src/pages/HistoricalWorkspace.css');
      const host=document.createElement('div');document.querySelector('main').replaceChildren(host);
      const root=ReactDOM.createRoot(host);const now=new Date().toISOString();
      const data={total:895,completed:19,reused:9,built:10,unavailable:0,failed:0,active:2,queued:874,before:'2026-08-19',updated_at:now,elapsed_seconds:180,tickers_per_minute:6.3,eta_seconds:8300,
        workers:[{slot:1,ticker:'ACHR',state:'building',stage:'MLE fitting',session:'2026-05-04',completed:246,total:405,updated_at:now,resumed:200},
        {slot:2,ticker:'AESI',state:'building',stage:'ClickHouse OHLCV',session:'2026-07-13',completed:294,total:405,updated_at:now,retried:1}]};
      window.renderV7Review=(mode) => root.render(React.createElement(Modal,{title:'Backtest preparation',onClose:()=>root.render(null),closeOnBackdrop:true,className:'backtest-preparation-modal'},
        React.createElement('div',{className:'backtest-preparation-content'},React.createElement(FilteredV7Preparation,{progress:mode==='starting'?{...data,completed:0,built:0,reused:0,eta_seconds:null}:mode==='failed'?{...data,failed:1,active:0,workers:data.workers.map((w,i)=>({...w,state:i?'cancelled':'failed',error:i?'':'Canonical source changed during aggregation'}))}:data}))));
      window.renderV7Review('running');
    }""")
    dialog=page.get_by_role('dialog',name='Backtest preparation')
    dialog.wait_for()
    assert dialog.locator('tbody tr').count()==2
    assert '6.3 tickers/min' in dialog.inner_text()
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    for mode, text in [('running','MLE fitting'),('starting','Estimating'),('failed','Canonical source changed')]:
        page.evaluate('(mode)=>window.renderV7Review(mode)',mode)
        dialog.get_by_text(text,exact=False).first.wait_for()
        page.screenshot(path=str(screenshot_path.with_name(screenshot_path.stem+'__'+mode+'.png')),full_page=True)
    page.keyboard.press('Escape');dialog.wait_for(state='hidden')
    page.evaluate("window.renderV7Review('running')")
    dialog.wait_for()
