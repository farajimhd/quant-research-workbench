import { useEffect, useMemo, useState } from "react";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";

type Episode = {listing_id:string;ticker:string;pair_id:number;time_us:number;entry_target_us:number;selected:boolean;selection_reason:string;selection_score:number|null;best_score:number|null;group_id:number|null;allocation_ratio:number;entry_gain:number;close:number;score_threshold:number;start_us:number;reference_exit_us:number|null};
type Group = {group_id:number;start_us:number;end_us:number;members:number};
type Timeline = {start_us:number|null;end_us:number|null;begin_us:number|null;finish_us:number|null;rows:Episode[];groups:Group[];total:number;unavailable:number;truncated:boolean};
const clock=(us:number)=>new Date(us/1000).toLocaleTimeString("en-GB",{timeZone:"America/New_York",hour12:false});
const color=(id:number|null)=>id===null?"var(--muted-foreground)":`hsl(${(id*137.508)%360} 65% 48%)`;
export function EpisodeGroupingChart({jobId,focusedGroup,onGroup,onInspect}:{jobId:string;focusedGroup:string;onGroup:(id:string)=>void;onInspect:(row:Episode)=>void}) {
 const [data,setData]=useState<Timeline|null>(null),[start,setStart]=useState<number|null>(null),[seconds,setSeconds]=useState(300);
 const [search,setSearch]=useState(""),[rejected,setRejected]=useState(false),[log,setLog]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState("");
 useEffect(()=>{setStart(null);setData(null);},[jobId]);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setError("");
  researchApi<Timeline>(`/api/research/models/v6/market-preview/timeline${query({job_id:jobId,start_us:start??undefined,seconds,search,include_rejected:rejected})}`,{signal:abort.signal,timeoutMs:300000})
   .then(r=>{if(!abort.signal.aborted)setData(r);}).catch(e=>{if(!abort.signal.aborted)setError(String(e));}).finally(()=>{if(!abort.signal.aborted)setLoading(false);});
  return()=>abort.abort();
 },[jobId,start,seconds,search,rejected]);
 const layout=useMemo(()=>{
  const ends:number[]=[];
  const rows=(data?.rows??[]).map(row=>{let lane=ends.findIndex(end=>end<=row.time_us);if(lane<0)lane=ends.length;ends[lane]=row.entry_target_us;return {...row,lane,score:row.selection_score??row.best_score??0};});
  return {rows,lanes:Math.max(1,ends.length),maxScore:Math.max(.001,...rows.map(r=>r.score))};
 },[data]);
 const axisStart=data?.start_us??0,axisEnd=data?.end_us??1;
 const x=(t:number)=>70+Math.max(0,Math.min(1,(t-axisStart)/(axisEnd-axisStart)))*910;
 const height=(score:number)=>40*(log?Math.log1p(score/.001)/Math.log1p(layout.maxScore/.001):score/layout.maxScore);
 const activate=(event:React.KeyboardEvent,call:()=>void)=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();call();}};
 return <section className="research-grouping-chart" aria-label="Episode grouping chart">
  <h2>Episode grouping · time and score</h2>
  <div className="research-controls">
   <label>Window<select aria-label="Grouping chart window" value={seconds} onChange={e=>setSeconds(+e.target.value)}><option value="60">1 minute</option><option value="300">5 minutes</option><option value="900">15 minutes</option><option value="3600">60 minutes</option></select></label>
   <label>Find ticker<input aria-label="Grouping chart ticker" value={search} onChange={e=>setSearch(e.target.value)}/></label>
   <label>Height scale<select aria-label="Grouping chart scale" value={log?"log":"linear"} onChange={e=>setLog(e.target.value==="log")}><option value="linear">Linear score</option><option value="log">Log score</option></select></label>
   <button className="button secondary compact" disabled={loading||!data||axisStart<=(data.begin_us??0)} onClick={()=>setStart(Math.max(data!.begin_us!,axisStart-seconds*1e6))}>Previous episode window</button>
   <button className="button secondary compact" disabled={loading||!data||axisEnd>=(data.finish_us??0)} onClick={()=>setStart(axisStart+seconds*1e6)}>Next episode window</button>
   <button className="button secondary compact" disabled={loading||!data} onClick={()=>{setStart(data!.begin_us);onGroup("");}}>Session start</button>
  </div>
  <label className="research-preview-compare"><input aria-label="Grouping chart rejected" type="checkbox" checked={rejected} onChange={e=>setRejected(e.target.checked)}/>Show positive-score rejected episodes</label>
  <p>Width = ENTRY to its saved target-exit witness. Height = {log?"log-transformed":"linear"} net discounted score; overlap uses separate lanes, not summed profit. Color = group; gray outline = rejected. Dashed lane line = 0.1%; tiny boxes use a 2px visibility minimum. Click a box for candles or a group band to filter the decision table.</p>
  {error&&<p role="alert">{error}</p>}
  <p role="status">{loading?"Loading episode timeline…":data?.start_us?`${clock(axisStart)}–${clock(axisEnd)} ET · ${data.total.toLocaleString()} episodes · height maximum ${(layout.maxScore*100).toFixed(3)}%`:"No positive episodes"}</p>
  {data?.truncated&&<p role="alert">Showing the first 5,000 of {data.total.toLocaleString()} episodes. Narrow the window or filter a ticker to see the remainder.</p>}
  {!!data?.unavailable&&<p>{data.unavailable} episodes lack a saved target-exit witness and cannot be drawn.</p>}
  {!loading&&data?.start_us&&<>
   <div className="research-grouping-surface"><div className="research-grouping-axis"><svg height={80} preserveAspectRatio="none" viewBox="0 0 1000 80" role="group" aria-label="Group timeline">
    {[0,1,2,3,4].map(i=><g key={i}><text x={70+i*227.5} y={15} textAnchor="middle">{clock(axisStart+(axisEnd-axisStart)*i/4)}</text><line x1={70+i*227.5} x2={70+i*227.5} y1={20} y2={75}/></g>)}
    {data.groups.map(g=><g key={g.group_id} role="button" tabIndex={0} aria-label={`Inspect group ${g.group_id}`} onClick={()=>onGroup(String(g.group_id))} onKeyDown={e=>activate(e,()=>onGroup(String(g.group_id)))}><title>{`Group ${g.group_id} · ${clock(g.start_us)}–${clock(g.end_us)} · ${g.members} members`}</title><rect x={x(g.start_us)} y={28} width={Math.max(3,x(g.end_us)-x(g.start_us))} height={25} fill={color(g.group_id)} stroke={focusedGroup===String(g.group_id)?"var(--foreground)":"none"} strokeWidth={3}/><text x={x(g.start_us)+2} y={69}>#{g.group_id}</text></g>)}
   </svg></div>
   <div className="research-grouping-lanes"><svg preserveAspectRatio="none" viewBox={`0 0 1000 ${layout.lanes*52+12}`} height={layout.lanes*52+12} role="group" aria-label="Episode score boxes">
    {Array.from({length:layout.lanes},(_,i)=><g key={i}><text x={6} y={i*52+35}>Lane {i+1}</text><line x1={70} x2={980} y1={i*52+46-height(.001)} y2={i*52+46-height(.001)} strokeDasharray="3 4"/></g>)}
    {layout.rows.map(r=><g key={`${r.listing_id}:${r.pair_id}`} role="button" tabIndex={0} aria-label={`Inspect ${r.ticker} pair ${r.pair_id}`} onClick={()=>onInspect(r)} onKeyDown={e=>activate(e,()=>onInspect(r))} opacity={focusedGroup&&focusedGroup!==String(r.group_id)?.toString()?0.25:1}>
     <title>{`${r.ticker} · pair ${r.pair_id}\n${clock(r.time_us)} → ${clock(r.entry_target_us)} · ${(r.entry_target_us-r.time_us)/1e6}s\nScore ${(r.score*100).toFixed(4)}% · gain $${r.entry_gain.toFixed(4)}/share\n${r.selected?`Group ${r.group_id} · ${(r.allocation_ratio*100).toFixed(3)}% cash`:r.selection_reason}`}</title>
     <rect x={x(r.time_us)} y={r.lane*52+46-Math.max(2,height(r.score))} width={Math.max(2,x(r.entry_target_us)-x(r.time_us))} height={Math.max(2,height(r.score))} fill={r.selected?color(r.group_id):"none"} fillOpacity={.65} stroke={color(r.group_id)} strokeWidth={focusedGroup===String(r.group_id)?2:1} strokeDasharray={r.selected?undefined:"3 2"}/>
     {x(r.entry_target_us)-x(r.time_us)>55&&<text x={x(r.time_us)+2} y={r.lane*52+12}>{r.ticker}</text>}
    </g>)}
   </svg></div></div>
   {data.total===0&&<p>No episodes overlap this window and filter.</p>}
  </>}
 </section>;
}
