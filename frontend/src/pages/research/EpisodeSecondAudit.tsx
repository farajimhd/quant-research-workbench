import { useEffect, useState } from "react";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";

type Row = {
 listing_id:string; ticker:string; pair_id:number; time_us:number; close:number;
 entry_gain:number; action:string; score:number; selected:boolean; selection_reason:string;
 group_id:number|null; allocation_ratio:number; group_contributor:boolean; passes_threshold:boolean;
};
type Window = {timeframe:string; time_us:number|null; clocks:number[]; total:number; rows:Row[]};
const clock=(us:number)=>new Date(us/1000).toLocaleTimeString("en-GB",{timeZone:"America/New_York",hour12:false});
export function EpisodeSecondAudit({jobId,onInspect}:{jobId:string;onInspect:(row:Row)=>void}) {
 const [data,setData]=useState<Window|null>(null),[time,setTime]=useState<number|null>(null);
 const [search,setSearch]=useState(""),[selection,setSelection]=useState("all"),[minimum,setMinimum]=useState(0),[offset,setOffset]=useState(0);
 const [error,setError]=useState(""),[loading,setLoading]=useState(false);
 useEffect(()=>{setTime(null);setOffset(0);},[jobId]);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setError("");
  researchApi<Window>(`/api/research/models/v6/market-preview/rows${query({job_id:jobId,time_us:time??undefined,search,selection,minimum_score:minimum/100,offset})}`,{signal:abort.signal,timeoutMs:300000})
   .then(r=>{if(!abort.signal.aborted)setData(r);}).catch(e=>{if(!abort.signal.aborted)setError(String(e));}).finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[jobId,time,search,selection,minimum,offset]);
 const index=data?.clocks.indexOf(data.time_us??-1)??-1;
 const move=(delta:number)=>{const next=data?.clocks[index+delta];if(next!==undefined){setTime(next);setOffset(0);}};
 const reset=()=>{setTime(null);setOffset(0);};
 return <section aria-label="Positive episode rows" className="research-second-audit">
  <h2>Positive episode rows · 1s</h2>
  <p>All positive-score 1a candle rows at this close, including rows below the selection floor. Only the first qualifying row per pair contributes to grouping; later rows share its pair assignment.</p>
  <div className="research-controls">
   <label>Find ticker<input aria-label="Episode row ticker" value={search} onChange={e=>{setSearch(e.target.value);reset();}}/></label>
   <label>Pair selection<select aria-label="Episode row selection" value={selection} onChange={e=>{setSelection(e.target.value);reset();}}><option value="all">All positive rows</option><option value="selected">Selected pairs</option><option value="rejected">Rejected pairs</option></select></label>
   <label>Minimum score · %<input aria-label="Episode row minimum score" type="number" min="0" max="100" step="0.01" value={minimum} onChange={e=>{setMinimum(+e.target.value);reset();}}/></label>
   <button className="button secondary compact" disabled={loading||index<=0} onClick={()=>move(-1)}>Previous 1s close</button>
   <label>Close ET<input aria-label="Episode row close" type="time" step="1" value={data?.time_us?clock(data.time_us):""} onChange={e=>{const found=data?.clocks.find(t=>clock(t)===e.target.value);if(found!==undefined){setTime(found);setOffset(0);}}}/></label>
   <button className="button secondary compact" disabled={loading||!data?.clocks.length||index>=data.clocks.length-1} onClick={()=>move(1)}>Next 1s close</button>
  </div>
  {error&&<p role="alert">{error}</p>}
  <p role="status">{loading?"Loading positive rows…":`${data?.total??0} rows · close ${index+1} / ${data?.clocks.length??0} with matching rows`}</p>
  {!loading&&data&&<><div className="research-preview-table"><table><thead><tr><th>Ticker / pair</th><th>Close ET</th><th>Gain $/share</th><th>Score %</th><th>1a action</th><th>Row passes floor</th><th>Pair selected</th><th>Group</th><th>Group contributor</th><th>Cash ratio</th></tr></thead><tbody>{data.rows.map(r=><tr key={`${r.listing_id}:${r.pair_id}:${r.time_us}`}><td><button className="research-ticker-button" onClick={()=>onInspect(r)}>{r.ticker} · {r.listing_id.split(":").at(-2)} / {r.pair_id}</button></td><td>{clock(r.time_us)}</td><td>{r.entry_gain.toFixed(6)}</td><td>{(r.score*100).toFixed(4)}%</td><td>{r.action}</td><td>{r.passes_threshold?"Yes":"No"}</td><td>{r.selected?"Yes":"No"}</td><td>{r.group_id??"—"}</td><td>{r.group_contributor?"First qualifying row":"—"}</td><td>{(r.allocation_ratio*100).toFixed(3)}%</td></tr>)}</tbody></table></div>
   {data.total===0&&<p>No positive candidate rows match this close and these filters.</p>}
   <div className="research-controls"><button className="button secondary compact" disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-100))}>Previous candidate rows</button><span>{data.total?offset+1:0}–{Math.min(offset+100,data.total)} / {data.total}</span><button className="button secondary compact" disabled={offset+100>=data.total} onClick={()=>setOffset(offset+100)}>Next candidate rows</button></div>
  </>}
 </section>;
}
