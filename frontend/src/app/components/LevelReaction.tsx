import {useEffect, useMemo, useRef, useState} from 'react';
import type {IChartApi, ISeriesApi, ISeriesPrimitive, IPrimitivePaneView, Time} from 'lightweight-charts';
import './levelReaction.css';

type Candle = {time:number; endTime?:number; low:number};
type Model = {id:string; ticker:string; dates:string[]; ready:boolean};
type Result = {side:string; level:{id:string; lower:number; upper:number; price:number}; probabilities:Record<string,number>};
export type ReactionRecord = {as_of:number; available_at:number; candle_start:number; candle_close:number; status:string; reason:string|null; price_age:number|null; results:Result[]; winner:{direction:'up'|'down'; probability:number}|null};
type Batch = {model_id:string; ticker:string; session_date:string; cutoff:string; as_of:number; horizon_seconds:number; model_hash:string; book_hash:string; input_hash:string; book_session:string; price_basis:string; records:ReactionRecord[]; available:number; requested:number};
const durations:Record<string,number>={'1s':1,'5s':5,'10s':10,'30s':30,'1m':60,'5m':300,'1h':3600};
const et=(t:number)=>new Date(t*1000).toLocaleTimeString('en-GB',{timeZone:'America/New_York',hour12:false});
const EMPTY:Candle[]=[];

export function useLevelReaction(ticker:string, sessionDate:string|undefined, asOf:string|undefined, timeframe:string, candles:Candle[]=EMPTY) {
  const [enabled,setEnabled]=useState(false);
  const [models,setModels]=useState<Model[]>([]);
  const [chosen,setChosen]=useState('');
  const [value,setValue]=useState<{key:string; data:Batch}>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const duration=durations[timeframe];
  const cutoff=asOf ? Date.parse(asOf)/1000 : NaN;
  const eligible=models.filter(m=>m.ready && m.ticker===ticker.toUpperCase() && m.dates.includes(sessionDate??''));
  const model=eligible.find(m=>m.id===chosen) ?? eligible.at(-1);
  const key=JSON.stringify([ticker,sessionDate,timeframe,model?.id]);
  const secondsET=Number.isFinite(cutoff)?et(cutoff).split(':').map(Number):[];
  const opening=Math.floor(cutoff)-(secondsET[0]*3600+secondsET[1]*60+secondsET[2])+4*3600;
  const closes=useMemo(()=>duration && Number.isFinite(cutoff) ? candles.map(c=>c.endTime??c.time+duration).filter(t=>Number.isInteger(t) && t>opening && t<=cutoff && (t-opening)%duration===0) : [],[candles,duration,cutoff]);
  const timesKey=JSON.stringify(closes);
  const desired=useRef({closes,sessionDate,cutoff,modelId:model?.id,ticker,duration});
  desired.current={closes,sessionDate,cutoff,modelId:model?.id,ticker,duration};
  useEffect(()=>{
    if(!enabled)return;
    const controller=new AbortController();
    fetch('/api/research/level-reaction/models',{signal:controller.signal}).then(async r=>{
      if(!r.ok)throw new Error('Model catalog unavailable');
      const result=await r.json();if(!controller.signal.aborted)setModels(result);
    }).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return()=>controller.abort();
  },[enabled,ticker,sessionDate]);
  useEffect(()=>{
    if(!enabled || !model || !duration)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;let completed='';
    const run=async()=>{
      const next=desired.current;
      const signature=JSON.stringify(next.closes);
      try {
        if(next.closes.length && next.sessionDate && Number.isFinite(next.cutoff) && signature!==completed){
          setBusy(true);setError('');
          const response=await fetch('/api/research/level-reaction/series',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:next.modelId,ticker:next.ticker,session_date:next.sessionDate,time_et:et(Math.max(...next.closes)),close_times:next.closes,timeframe_seconds:next.duration})});
          if(response.status!==429){
            const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Prediction unavailable');
            if(!controller.signal.aborted){setValue({key,data});setBusy(false);completed=signature;}
          }
        }
      }catch(e){if(!controller.signal.aborted){setError(String(e));setValue(undefined);setBusy(false);}}
      if(!controller.signal.aborted)timer=setTimeout(()=>void run(),300);
    };
    void run();
    return()=>{controller.abort();if(timer)clearTimeout(timer);};
  },[enabled,key]);
  const data=enabled && value?.key===key ? value.data : undefined;
  // Synchronous cutoff on rewind, even while a newer request is in flight.
  const rows=useMemo(()=>{const visible=new Set(closes);return data?.records.filter(r=>r.status==='ready' && r.available_at<=cutoff && visible.has(r.candle_close))??[];},[data,cutoff,timesKey]);
  const cursorDay=Number.isFinite(cutoff)?new Date(cutoff*1000).toLocaleDateString('en-CA',{timeZone:'America/New_York'}):'';
  const status=cursorDay && sessionDate && cursorDay!==sessionDate?'Chart cursor and candle session do not match':!duration?'Requires a timeframe from 1s through 1h':!Number.isFinite(cutoff)?'Requires a Debug or Backtest cursor':!model?'No prepared forward model for this ticker/session':error || (busy?'Loading predictions…':data?`${data.available}/${data.requested} candles · 60s horizon`:'Waiting for completed candles');
  return {rows,data,modelId:model?.id,controls:<span className="level-reaction-controls"><label className="toolbar-button"><input type="checkbox" aria-label="Show level reaction" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Level reaction</label>{enabled && <><select className="level-reaction-model" aria-label="Reaction model" value={model?.id??''} onChange={e=>setChosen(e.target.value)}>{!eligible.length && <option value="">No prepared model</option>}{eligible.map(m=><option key={m.id} value={m.id}>{m.id}</option>)}</select><span className="level-reaction-status" role="status" title={status}>{status}</span></>}</span>};
}

/** DOM labels are anchored by the native pane renderer. No price-scale changes. */
export class LevelReactionPrimitive implements ISeriesPrimitive<Time> {
  private chart?:IChartApi;private series?:ISeriesApi<'Candlestick'>;private host?:HTMLDivElement;
  private update?:()=>void;private rows:ReactionRecord[]=[];private data?:Batch;
  private candles=new Map<number,Candle>();private coordinate:(t:number)=>number|null=()=>null;
  private labels=new Map<number,HTMLButtonElement>();private tooltip?:HTMLDivElement;
  private selected?:number;private hideTimer?:ReturnType<typeof setTimeout>;
  private paneHeight=0;
  private view:IPrimitivePaneView={zOrder:()=> 'top',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(({mediaSize})=>{
    if(!this.host || !this.series)return;
    this.paneHeight=mediaSize.height;
    const visible=new Set<number>();
    const range=this.chart?.timeScale().getVisibleRange();
    for(const row of this.rows){
      if(range && typeof range.from==='number' && row.candle_start<range.from)continue;
      if(range && typeof range.to==='number' && row.candle_start>range.to)break;
      const candle=this.candles.get(row.candle_start);if(!candle || !row.winner)continue;
      const x=this.coordinate(row.candle_start),y=this.series.priceToCoordinate(candle.low);
      if(x===null || y===null || x<0 || x>mediaSize.width || y<0 || y+20>mediaSize.height)continue;
      visible.add(row.candle_close);
      let button=this.labels.get(row.candle_close);
      if(!button){button=document.createElement('button');button.type='button';button.className='level-reaction-label';
        button.dataset.close=String(row.candle_close);button.dataset.start=String(row.candle_start);
        button.onmouseenter=()=>this.show(row);button.onfocus=()=>this.show(row);
        button.onmouseleave=()=>this.scheduleHide();button.onblur=()=>this.scheduleHide();
        this.host.appendChild(button);this.labels.set(row.candle_close,button);}
      button.textContent=`${row.winner.direction==='up'?'↑':'↓'} ${Math.round(row.winner.probability*100)}%`;
      button.dataset.direction=row.winner.direction;
      // Native coordinates are pane-local; the DOM host includes the left axis.
      button.style.left=`${x+(this.chart?.priceScale('left').width()??0)}px`;button.style.top=`${y+5}px`;
    }
    for(const [time,button] of this.labels){if(!visible.has(time)){button.remove();this.labels.delete(time);if(time===this.selected)this.hide();}}
    if(this.selected!==undefined)this.positionTooltip();
  })})};
  private scheduleHide(){this.hideTimer=setTimeout(()=>this.hide(),200);}
  private hide(){if(this.hideTimer)clearTimeout(this.hideTimer);this.tooltip?.remove();this.tooltip=undefined;this.selected=undefined;}
  private show(row:ReactionRecord){
    this.hide();if(!this.host || !this.data)return;this.selected=row.candle_close;
    const tip=document.createElement('div');tip.className='level-reaction-tooltip';tip.setAttribute('role','tooltip');
    tip.tabIndex=0;
    tip.onmouseenter=()=>{if(this.hideTimer)clearTimeout(this.hideTimer);};
    tip.onmouseleave=()=>this.scheduleHide();tip.onfocus=()=>{if(this.hideTimer)clearTimeout(this.hideTimer);};
    tip.onblur=()=>this.scheduleHide();
    const heading=document.createElement('div');heading.textContent=`${et(row.as_of)} ET · next ${this.data.horizon_seconds}s · price age ${row.price_age}s`;
    const meaning=document.createElement('p');meaning.textContent='↑ upper break · ↓ lower break. Separate probabilities, not complements.';
    const grid=document.createElement('div');grid.className='level-reaction-outcomes';
    for(const r of row.results){
      const section=document.createElement('section');
      const title=document.createElement('strong');title.textContent=`${r.side.toUpperCase()} $${r.level.lower.toFixed(4)}–$${r.level.upper.toFixed(4)}`;
      section.appendChild(title);
      for(const [label,p] of Object.entries(r.probabilities)){
        const line=document.createElement('div');line.textContent=`${label.replaceAll('_',' ')}: ${(p*100).toFixed(1)}%`;section.appendChild(line);
      }
      grid.appendChild(section);
    }
    const provenance=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Model and data provenance';
    const content=document.createElement('div');content.textContent=`Model ${this.data.model_id} · trained through ${this.data.cutoff}\nBook ${this.data.book_session} · ${this.data.price_basis}\nModel hash ${this.data.model_hash}\nBook hash ${this.data.book_hash}\nInput hash ${this.data.input_hash}`;
    provenance.append(summary,content);tip.append(heading,meaning,grid,provenance);
    this.host.appendChild(tip);this.tooltip=tip;this.positionTooltip();
  }
  private positionTooltip(){if(!this.tooltip || !this.host)return;this.tooltip.style.left='8px';this.tooltip.style.top='8px';this.tooltip.style.maxHeight=`${Math.max(20,this.paneHeight-16)}px`;}
  attached({chart,series,requestUpdate}:Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]){
    this.chart=chart;this.series=series as ISeriesApi<'Candlestick'>;this.update=requestUpdate;
    this.host=document.createElement('div');this.host.className='level-reaction-layer';chart.chartElement().appendChild(this.host);
  }
  detached(){this.host?.remove();this.host=undefined;this.labels.clear();this.hide();this.chart=undefined;this.series=undefined;this.update=undefined;}
  paneViews(){return [this.view];}
  setState(rows:ReactionRecord[],data:Batch|undefined,candles:Candle[],coordinate:(t:number)=>number|null){
    if(data!==this.data){this.labels.forEach(b=>b.remove());this.labels.clear();this.hide();}
    this.rows=rows;this.data=data;this.candles=new Map(candles.map(c=>[c.time,c]));this.coordinate=coordinate;this.update?.();
  }
}
