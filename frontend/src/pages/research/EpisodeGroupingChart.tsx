import { useEffect, useMemo, useRef, useState } from "react";
import { query } from "../../api/client";
import { researchApi } from "./researchApi";

type Episode = {listing_id:string;ticker:string;pair_id:number;time_us:number;entry_target_us:number;selected:boolean;selection_reason:string;selection_score:number|null;best_score:number|null;group_id:number|null;allocation_ratio:number;entry_gain:number;close:number;score_threshold:number;start_us:number;reference_exit_us:number|null};
type Group = {group_id:number;start_us:number;end_us:number;target_end_us:number;members:number};
type Timeline = {start_us:number|null;end_us:number|null;begin_us:number|null;finish_us:number|null;rows:Episode[];groups:Group[];total:number;unavailable:number;truncated:boolean};
const clock=(us:number)=>new Date(us/1000).toLocaleTimeString("en-GB",{timeZone:"America/New_York",hour12:false});
const color=(id:number|null)=>id===null?"var(--muted-foreground)":`hsl(${(id*137.508)%360} 65% 48%)`;
export function EpisodeGroupingChart({jobId,sessionScore,focusedGroup,onGroup,onInspect}:{jobId:string;sessionScore:number;focusedGroup:string;onGroup:(id:string)=>void;onInspect:(row:Episode)=>void}) {
 const surface=useRef<HTMLElement>(null);
 const gesture=useRef<{x:number;start:number;dragged:boolean}|null>(null);
 const suppressClick=useRef(false);
 const [width,setWidth]=useState(1000);
 useEffect(()=>{const node=surface.current;if(!node)return;const observer=new ResizeObserver(([entry])=>setWidth(Math.max(900,entry.contentRect.width)));observer.observe(node);return()=>observer.disconnect();},[]);
 const [data,setData]=useState<Timeline|null>(null),[start,setStart]=useState<number|null>(null),[seconds,setSeconds]=useState(300);
 const [search,setSearch]=useState(""),[rejected,setRejected]=useState(true),[log,setLog]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState("");
 useEffect(()=>{setStart(null);setData(null);},[jobId]);
 useEffect(()=>{
  const abort=new AbortController();setLoading(true);setError("");
  const timer=window.setTimeout(()=>researchApi<Timeline>(`/api/research/models/v6/market-preview/timeline${query({job_id:jobId,start_us:start??undefined,seconds,search,include_rejected:rejected})}`,{signal:abort.signal,timeoutMs:300000})
   .then(r=>{if(!abort.signal.aborted)setData(r);}).catch(e=>{if(!abort.signal.aborted)setError(String(e));}).finally(()=>{if(!abort.signal.aborted)setLoading(false);}),120);
  return()=>{window.clearTimeout(timer);abort.abort();};
 },[jobId,start,seconds,search,rejected]);
 const layout=useMemo(()=>{
  const ends:number[]=[];
  const rows=(data?.rows??[]).map(row=>{let lane=ends.findIndex(end=>end<=row.time_us);if(lane<0)lane=ends.length;ends[lane]=row.entry_target_us;return {...row,lane,score:row.selection_score??row.best_score??0};});
  return {rows,lanes:Math.max(1,ends.length),maxScore:Math.max(.001,...rows.map(r=>r.score))};
 },[data]);
 const bands=useMemo(()=>{const ends:number[]=[];const rows=(data?.groups??[]).map(g=>{let lane=ends.findIndex(end=>end<=g.start_us);if(lane<0)lane=ends.length;ends[lane]=g.target_end_us;return {...g,lane};});return {rows,height:Math.max(1,ends.length)*32+38};},[data]);
 const axisStart=start??data?.start_us??0,axisEnd=axisStart+seconds*1e6;
 const x=(t:number)=>70+Math.max(0,Math.min(1,(t-axisStart)/(axisEnd-axisStart)))*(width-120);
 const height=(score:number)=>40*(log?Math.log1p(score/.001)/Math.log1p(layout.maxScore/.001):score/layout.maxScore);
 const tickSeconds=seconds<=60?15:seconds<=300?60:seconds<=900?180:600;
 const ticks=Array.from({length:Math.floor(seconds/tickSeconds)+1},(_,i)=>axisStart+i*tickSeconds*1e6);
 const zoom=(factor:number,anchor=.5)=>{if(!data)return;const next=Math.max(60,Math.min(3600,Math.round(seconds*factor)));setStart(Math.max(data.begin_us??0,Math.round(axisStart+(seconds-next)*anchor*1e6)));setSeconds(next);};
 useEffect(()=>{const node=surface.current;if(!node)return;const wheel=(e:WheelEvent)=>{if(!(e.target as Element).closest('.research-grouping-surface')||!data)return;e.preventDefault();const svg=node.querySelector('.research-grouping-axis svg')!;const bounds=svg.getBoundingClientRect();const anchor=Math.max(0,Math.min(1,((e.clientX-bounds.left)*width/bounds.width-70)/(width-120)));zoom(e.deltaY>0?1.25:.8,anchor);};node.addEventListener('wheel',wheel,{passive:false});return()=>node.removeEventListener('wheel',wheel);},[data,loading,seconds,width]);
 const activate=(event:React.KeyboardEvent,call:()=>void)=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();call();}};
 return <section ref={surface} className="research-grouping-chart" aria-label="Episode grouping chart">
  <h2>Episode grouping · time and score</h2>
  <div className="research-controls">
   <label>Window<select aria-label="Grouping chart window" value={seconds} onChange={e=>setSeconds(+e.target.value)}>{![60,300,900,3600].includes(seconds)&&<option value={seconds}>{seconds} seconds</option>}<option value="60">1 minute</option><option value="300">5 minutes</option><option value="900">15 minutes</option><option value="3600">60 minutes</option></select></label>
   <label>Find ticker<input aria-label="Grouping chart ticker" value={search} onChange={e=>setSearch(e.target.value)}/></label>
   <label>Height scale<select aria-label="Grouping chart scale" value={log?"log":"linear"} onChange={e=>setLog(e.target.value==="log")}><option value="linear">Linear score</option><option value="log">Log score</option></select></label>
   <button className="button secondary compact" disabled={loading||!data||seconds<=60} onClick={()=>zoom(.5)}>Zoom in</button>
   <button className="button secondary compact" disabled={loading||!data||seconds>=3600} onClick={()=>zoom(2)}>Zoom out</button>
   <button className="button secondary compact" disabled={loading||!data} onClick={()=>{setStart(data!.begin_us);onGroup("");}}>Session start</button>
  </div>
  <label className="research-preview-compare"><input aria-label="Grouping chart rejected" type="checkbox" checked={rejected} onChange={e=>setRejected(e.target.checked)}/>Show positive-score rejected episodes</label>
  <p className="research-grouping-summary"><strong>Session selected score sum: {(sessionScore*100).toLocaleString(undefined,{maximumFractionDigits:3})}%</strong><span>Sum of selected episode scores · whole session · not cash-weighted P&amp;L</span></p>
  <details className="research-grouping-help"><summary>Chart guide</summary><p>Width = elapsed ENTRY-to-target time; height = score. Group bands span earliest ENTRY to latest target. Color = group, red = threshold rejected, gray = other rejected. Drag horizontally to pan; scroll to zoom. Lanes show overlaps. Click a box for candles or a band for its group. Dashed line = 0.1%; minimum visible height = 2px.</p></details>
  {error&&<p role="alert">{error}</p>}
  <p role="status">{loading?"Loading episode timeline…":data?.start_us?`${clock(axisStart)}–${clock(axisEnd)} ET · ${data.total.toLocaleString()} episodes · height maximum ${(layout.maxScore*100).toFixed(3)}%`:"No positive episodes"}</p>
  {data?.truncated&&<p role="alert">Showing the first 5,000 of {data.total.toLocaleString()} episodes. Narrow the window or filter a ticker to see the remainder.</p>}
  {!!data?.unavailable&&<p>{data.unavailable} episodes lack a saved target-exit witness and cannot be drawn.</p>}
  {data?.start_us&&<>
   <div className="research-grouping-surface" aria-label="Pan and zoom episode timeline"
    onPointerDown={e=>{if(e.button!==0)return;suppressClick.current=false;gesture.current={x:e.clientX,start:axisStart,dragged:false};}}
    onPointerMove={e=>{const g=gesture.current;if(g&&Math.abs(e.clientX-g.x)>5){g.dragged=true;e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.style.cursor="grabbing";const bounds=e.currentTarget.querySelector("svg")!.getBoundingClientRect();setStart(Math.max(data.begin_us??0,Math.round(g.start-(e.clientX-g.x)*width/bounds.width/(width-120)*seconds*1e6)));}}}
    onPointerUp={e=>{const g=gesture.current;gesture.current=null;e.currentTarget.style.cursor="";if(g?.dragged){suppressClick.current=true;const bounds=e.currentTarget.querySelector('svg')!.getBoundingClientRect();setStart(Math.max(data.begin_us??0,Math.round(g.start-(e.clientX-g.x)*width/bounds.width/(width-120)*seconds*1e6)));}}}
    onPointerCancel={()=>{gesture.current=null;}}
    onClickCapture={e=>{if(suppressClick.current){e.preventDefault();e.stopPropagation();suppressClick.current=false;}}}><div className="research-grouping-axis"><svg height={bands.height} width={width} viewBox={`0 0 ${width} ${bands.height}`} role="group" aria-label="Group timeline">
    {ticks.map(t=><g key={t}><text x={x(t)} y={15} textAnchor="middle">{clock(t)}</text><line x1={x(t)} x2={x(t)} y1={20} y2={bands.height}/></g>)}
    {bands.rows.filter(g=>g.start_us<axisEnd&&g.target_end_us>axisStart).map(g=><g key={g.group_id} role="button" tabIndex={0} aria-label={`Inspect group ${g.group_id}`} onClick={()=>onGroup(String(g.group_id))} onKeyDown={e=>activate(e,()=>onGroup(String(g.group_id)))}><title>{`Group ${g.group_id} · ${clock(g.start_us)}–${clock(g.target_end_us)} · ${g.members} members`}</title><rect x={x(g.start_us)} y={28+g.lane*32} width={Math.max(3,x(g.target_end_us)-x(g.start_us))} height={25} fill={color(g.group_id)} stroke={focusedGroup===String(g.group_id)?"var(--foreground)":"none"} strokeWidth={3}/><text x={x(g.start_us)+2} y={44+g.lane*32}>#{g.group_id}</text></g>)}
   </svg></div>
   <div className="research-grouping-lanes"><svg width={width} viewBox={`0 0 ${width} ${layout.lanes*52+12}`} height={layout.lanes*52+12} role="group" aria-label="Episode score boxes">
    {ticks.map(t=><line key={t} x1={x(t)} x2={x(t)} y1={0} y2={layout.lanes*52+12}/>)}
    {Array.from({length:layout.lanes},(_,i)=><g key={i}><text x={6} y={i*52+35}>Lane {i+1}</text><line x1={70} x2={width-50} y1={i*52+46-height(.001)} y2={i*52+46-height(.001)} strokeDasharray="3 4"/></g>)}
    {layout.rows.filter(r=>r.time_us<axisEnd&&r.entry_target_us>axisStart).map(r=><g key={`${r.listing_id}:${r.pair_id}`} role="button" tabIndex={0} aria-label={`Inspect ${r.ticker} pair ${r.pair_id}`} onClick={()=>onInspect(r)} onKeyDown={e=>activate(e,()=>onInspect(r))} opacity={focusedGroup&&focusedGroup!==String(r.group_id)?0.25:1}>
     <title>{`${r.ticker} · pair ${r.pair_id}\n${clock(r.time_us)} → ${clock(r.entry_target_us)} · ${(r.entry_target_us-r.time_us)/1e6}s\nScore ${(r.score*100).toFixed(4)}% · gain $${r.entry_gain.toFixed(4)}/share\n${r.selected?`Group ${r.group_id} · ${(r.allocation_ratio*100).toFixed(3)}% cash`:r.selection_reason}`}</title>
     <rect x={x(r.time_us)} y={r.lane*52+46-Math.max(2,height(r.score))} width={Math.max(2,x(r.entry_target_us)-x(r.time_us))} height={Math.max(2,height(r.score))} fill={r.selected?color(r.group_id):r.selection_reason==="below_score_threshold"?"var(--research-threshold-rejected)":"none"} fillOpacity={.65} stroke={r.selection_reason==="below_score_threshold"?"var(--research-threshold-rejected)":color(r.group_id)} strokeWidth={focusedGroup===String(r.group_id)?2:1} strokeDasharray={r.selected?undefined:"3 2"}/>
     {x(r.entry_target_us)-x(r.time_us)>55&&<text x={x(r.time_us)+2} y={r.lane*52+12}>{r.ticker}</text>}
    </g>)}
   </svg></div></div>
   {data.total===0&&<p>No episodes overlap this window and filter.</p>}
  </>}
 </section>;
}
