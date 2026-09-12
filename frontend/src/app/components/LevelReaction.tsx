import { useEffect, useRef, useState } from "react";
import { Modal } from "./Modal";

type Model = { id: string; ticker: string; cutoff: string; dates: string[]; ready: boolean; status: {stage: string; completed: number; total: number} };
type Candle = {t: number; open: number; high: number; low: number; close: number};
type Result = {side: string; level: {id: string; lower: number; upper: number; price: number}; probabilities: Record<string, number>; up: number; down: number};
type Prediction = {ticker: string; as_of: number; cutoff: string; horizon_seconds: number; price: number; price_age: number; model_hash: string; book_hash: string; book_session: string; price_basis: string; seconds: number; candles: Candle[]; results: Result[]};
const percent = (v: number) => `${(100*v).toFixed(1)}%`;
const et = (stamp: number) => new Date(stamp*1000).toLocaleTimeString("en-GB", {timeZone: "America/New_York", hour12: false});

function Snapshot({value}: {value: Prediction}) {
  const candles = value.candles;
  if (!candles.length) return null;
  const prices = [...candles.flatMap(c => [c.low, c.high]), ...value.results.flatMap(r => [r.level.lower, r.level.upper])];
  const min = Math.min(...prices), max = Math.max(...prices), span = Math.max(.01, max-min);
  const begin = candles[0].t, duration = Math.max(1, value.as_of-begin);
  const x = (t: number) => 64+780*(t-begin)/duration;
  const y = (p: number) => 240-210*(p-min)/span;
  return <svg className="level-reaction-snapshot" viewBox="0 0 960 280" role="img" aria-label="Past-only price snapshot with upper and lower historical bands">
    {[0,.25,.5,.75,1].map(f => <g key={f}><line x1="64" x2="844" y1={y(min+span*f)} y2={y(min+span*f)} className="reaction-grid"/><text x="4" y={y(min+span*f)+4}>{(min+span*f).toFixed(2)}</text></g>)}
    {value.results.map(r => <g key={r.side} className={`reaction-${r.side}`}><rect x="64" width="780" y={y(r.level.upper)} height={Math.max(2,y(r.level.lower)-y(r.level.upper))}/><line x1="64" x2="844" y1={y(r.level.price)} y2={y(r.level.price)}/><text x="854" y={y(r.level.price)+(r.side==='upper'?-5:13)}>{r.side} {r.level.price.toFixed(2)}</text></g>)}
    <path className="reaction-price" d={candles.map((c,i)=>`${i && c.t-candles[i-1].t<=5?'L':'M'}${x(c.t)},${y(c.close)}`).join(' ')}/>
    <text x="64" y="268">{et(begin)} ET</text><text x="844" y="268" textAnchor="end">{et(value.as_of)} · prediction time</text>
  </svg>;
}

export function useLevelReaction(ticker?: string, sessionDate?: string, asOf?: string) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [day, setDay] = useState(sessionDate ?? "");
  const [clock, setClock] = useState("07:10:46");
  const [value, setValue] = useState<Prediction>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  const clear = () => { request.current?.abort(); setValue(undefined); setError(""); setBusy(false); };
  useEffect(() => { clear(); setModelId(""); setDay(sessionDate ?? ""); }, [ticker, sessionDate]);
  useEffect(() => { clear(); }, [modelId, day, clock]);
  useEffect(() => {
    if (!open) { request.current?.abort(); return; }
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await fetch('/api/research/level-reaction/models', {signal:controller.signal});
        if (!response.ok) throw new Error('Model catalog unavailable');
        const all: Model[] = await response.json();
        const choices = all.filter(m=>m.ticker === ticker?.toUpperCase());
        setModels(choices);
        setModelId(id=>choices.some(m=>m.id===id)?id:choices.filter(m=>m.ready).at(-1)?.id ?? choices.at(-1)?.id ?? "");
      } catch (e) { if (!controller.signal.aborted) setError(String(e)); }
    };
    void refresh(); const timer = window.setInterval(refresh, 10000);
    return () => { controller.abort(); clearInterval(timer); request.current?.abort(); };
  }, [open, ticker]);
  const selected = models.find(m=>m.id===modelId);
  const available = Boolean(selected?.ready && selected.dates.includes(day));
  const run = async () => {
    clear(); const controller = new AbortController(); request.current = controller; setBusy(true);
    try {
      const response = await fetch('/api/research/level-reaction/predict', {method:'POST', signal:controller.signal, headers:{'Content-Type':'application/json'}, body:JSON.stringify({model_id:modelId,ticker,session_date:day,time_et:clock})});
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Invalid prediction request');
      if (!controller.signal.aborted) setValue(body);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return { controls: <><button type="button" className="toolbar-button level-reaction-toggle" onClick={()=>setOpen(true)} aria-haspopup="dialog">Level reaction</button>
    {open && <Modal title={`${ticker ?? ''} · Level reaction`} className="level-reaction-modal" onClose={()=>{clear();setOpen(false);}}>
      <p>Predict the next 60 seconds from the selected completed second. Historical bands and market inputs are limited to what was available then. Presentation only; strategy orders are unchanged.</p>
      <form className="level-reaction-form" onSubmit={e=>{e.preventDefault();void run();}}>
        <label>Trained model<select value={modelId} onChange={e=>setModelId(e.target.value)}>{!models.length && <option>No model for this ticker</option>}{models.map(m=><option key={m.id} value={m.id}>{m.id}</option>)}</select></label>
        <label>Session<input type="date" value={day} onChange={e=>setDay(e.target.value)}/></label>
        <label>Prediction time · ET<input type="time" step="1" value={clock} onChange={e=>setClock(e.target.value.length===5?e.target.value+':00':e.target.value)}/></label>
        <button type="submit" className="toolbar-button" disabled={!available || busy || !clock}>{busy?'Running…':'Run prediction'}</button>
        {asOf && <button type="button" className="toolbar-button" onClick={()=>{const d=new Date(asOf);if(!Number.isNaN(d.getTime())) {setClock(et(d.getTime()/1000));setDay(d.toLocaleDateString('en-CA',{timeZone:'America/New_York'}));}}}>Use chart time</button>}
      </form>
      {selected && <p className="level-reaction-status">Trained through {selected.cutoff}. {selected.ready ? `Prepared forward sessions: ${selected.dates.join(', ') || 'test preparation in progress'}.` : `${selected.status.stage}: ${selected.status.completed}/${selected.status.total} sessions.`}</p>}
      {!available && selected?.ready && <p role="status">Choose a prepared date after the training cutoff. Earlier dates would leak training information.</p>}
      {error && <p role="alert">{error}</p>}
      {value && <section aria-live="polite"><h3>{day} · {et(value.as_of)} ET · ${value.price.toFixed(4)}</h3>
        <p>Price age {value.price_age.toFixed(0)}s · horizon {value.horizon_seconds}s · {value.price_basis}</p>
        {value.results.length===2 && value.results[0].level.id===value.results[1].level.id && <p>Price is inside one historical band. Both panels evaluate that same band from opposite sides; their probabilities are separate hypotheses, not values to add together.</p>}
        <Snapshot value={value}/>
        <div className="level-reaction-results">{value.results.map(r=><article key={r.side}><h4>{r.side === 'upper' ? 'Upper band' : 'Lower band'} · ${r.level.lower.toFixed(4)}–${r.level.upper.toFixed(4)}</h4>
          <div className="reaction-directions"><span>↑ Up <strong>{percent(r.up)}</strong></span><span>↓ Down <strong>{percent(r.down)}</strong></span></div>
          <dl>{Object.entries(r.probabilities).map(([label,p])=><div key={label}><dt>{label.replace('_',' ')}</dt><dd>{percent(p)}</dd></div>)}</dl>
          <p>{r.side==='upper'?'Up means break above; down means rejection below.':'Up means rejection above; down means break below.'} Not reached and unresolved remain separate outcomes.</p>
        </article>)}</div>
        <details><summary>Model and data provenance</summary><p>Book finalized {value.book_session}; model data through {value.cutoff}. Inference request {value.seconds.toFixed(2)}s.</p><p>Model: {value.model_hash}</p><p>Book: {value.book_hash}</p></details>
      </section>}
    </Modal>}
  </> };
}
