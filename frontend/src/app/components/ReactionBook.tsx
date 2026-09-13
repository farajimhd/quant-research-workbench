import {useEffect,useRef,useState} from 'react';
import type {IPrimitivePaneView,ISeriesApi,ISeriesPrimitive,Time} from 'lightweight-charts';

type Fit={status:string;count:number;coverage?:number;scale?:number};
type Segment={fit:Fit;id:string;price:number;lower:number;upper:number;role:string;historical:boolean;valid_from:number;valid_to:number;model_input:boolean;color?:string;category?:string;showBand?:boolean;showCenter?:boolean};
type Book={as_of:number;model_id?:string;book_id?:string;book_hash:string;book_version:string;segments:Segment[];historical_count:number;current_day_count:number;candidate_count:number;proposals:number;merged_proposals:number};
const EMPTY:Segment[]=[];

type Category='historicalResistance'|'currentResistance'|'historicalSupport'|'currentSupport';
const categories:Record<Category,string>={historicalResistance:'Historical resistance',currentResistance:'Current-day resistance',historicalSupport:'Historical support',currentSupport:'Current-day support'};
type Preferences=Partial<Record<Category|'transition',{hidden?:boolean;color?:string}>> & {transitionsHidden?:boolean;visible?:boolean;bandsVisible?:boolean;centersVisible?:boolean;minimumReactions?:number;maximumWidthBps?:number};
export function useReactionBook(ticker:string,day:string|undefined,asOf:string|undefined,enabled:boolean,mode:'history'|'live',storageKey:string,viewport?:{first:string;last:string}){
  const [preferences,setPreferences]=useState<Preferences>(()=>{try{return JSON.parse(localStorage.getItem(storageKey+'.v7')||'{}');}catch{return {};}});
  const [value,setValue]=useState<{key:string;book:Book}>();
  const [error,setError]=useState('');
  const key=JSON.stringify([ticker,day,mode]);
  const cutoff=asOf?Date.parse(asOf)/1000:mode==='live'?Date.now()/1000:NaN;
  const desired=useRef(cutoff);desired.current=cutoff;
  const priorCache=useRef(new Map<string,{next_before:string;segments:Segment[]}>());
  const [prior,setPrior]=useState<{key:string;segments:Segment[];count:number;error:string;loading:boolean}>();
  const firstDay=viewport?.first??day;
  const nextVisibleDay=viewport?new Date(Date.parse(viewport.last+'T12:00:00Z')+86400000).toISOString().slice(0,10):day;
  const beforeDay=day && nextVisibleDay && day<nextVisibleDay?day:nextVisibleDay;
  const priorKey=JSON.stringify([key,firstDay,beforeDay]);
  useEffect(()=>{
    if(!enabled || !firstDay || !beforeDay || firstDay>=beforeDay){setPrior(undefined);return;}
    const controller=new AbortController();
    const run=async()=>{
      let before=beforeDay,count=0;const collected:Segment[]=[];
      setPrior({key:priorKey,segments:[],count:0,error:'',loading:true});
      try{
        while(before>firstDay && !controller.signal.aborted){
          const cacheKey=JSON.stringify([ticker,mode,before]);
          let page=priorCache.current.get(cacheKey);
          if(!page){
            const response=await fetch('/api/research/level-book-v7/chart-checkpoint',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker,mode,session_date:before,time_et:'04:00:00'})});
            const result=await response.json();if(!response.ok)throw new Error(result.detail || 'Prior V7 checkpoint unavailable');
            if(result.purpose!=='historical_chart_only' || result.next_before>=before)throw new Error('Invalid V7 checkpoint page');
            page=result;priorCache.current.set(cacheKey,result);
            while(priorCache.current.size>32)priorCache.current.delete(priorCache.current.keys().next().value!);
          }
          before=page!.next_before;
          if(before>=firstDay){collected.push(...page!.segments);count++;}
          if(!controller.signal.aborted)setPrior({key:priorKey,segments:[...collected],count,error:'',loading:before>firstDay});
        }
      }catch(e){if(!controller.signal.aborted)setPrior({key:priorKey,segments:collected,count,error:String(e),loading:false});}
    };
    void run();return()=>controller.abort();
  },[enabled,priorKey]);
  useEffect(()=>{
    if(!enabled || !day)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;let completed=NaN;
    const run=async()=>{
      const stamp=Math.floor(desired.current);
      try{
        if(Number.isFinite(stamp) && stamp!==completed){
          const date=new Date(stamp*1000);
          if(date.toLocaleDateString('en-CA',{timeZone:'America/New_York'})!==day)throw new Error('Chart cursor and V7 session do not match');
          const response=await fetch('/api/research/level-book-v7/book',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({book_id:'level-book-v7',ticker,mode,session_date:day,time_et:date.toLocaleTimeString('en-GB',{timeZone:'America/New_York',hour12:false})})});
          const book=await response.json();if(!response.ok)throw new Error(book.detail || 'QMD V7 unavailable');
          if(!controller.signal.aborted && stamp<=desired.current){setValue({key,book});setError('');completed=stamp;}
        }
      }catch(e){if(!controller.signal.aborted){setError(String(e));setValue(undefined);}}
      if(!controller.signal.aborted)timer=setTimeout(()=>void run(),1000);
    };
    void run();return()=>{controller.abort();if(timer)clearTimeout(timer);};
  },[enabled,key]);
  const book=enabled && value?.key===key && value.book.as_of<=cutoff?value.book:undefined;
  const category=(s:Segment):Category=>`${s.historical?'historical':'current'}${s.role==='support'?'Support':'Resistance'}`;
  const visible=enabled && preferences.visible!==false;
  const transitionsHidden=preferences.transition?.hidden ?? preferences.transitionsHidden;
  const priorView=prior?.key===priorKey?prior:undefined;
  const presentation=[...(priorView?.segments??[]),...(book?.segments??[])];
  const minimumReactions=preferences.minimumReactions??0,maximumWidthBps=preferences.maximumWidthBps??0;
  const passesThresholds=(s:Segment)=>s.fit.count>=minimumReactions && (maximumWidthBps===0 || (s.upper-s.lower)/s.price*10000<=maximumWidthBps);
  const segments=visible?presentation.filter(passesThresholds).filter(s=>s.role==='transition'?!transitionsHidden:!preferences[category(s)]?.hidden).map(s=>({...s,
    color:preferences[s.role==='transition'?'transition':category(s)]?.color,category:s.role==='transition'?'transition':category(s),
    showBand:preferences.bandsVisible!==false,showCenter:preferences.centersVisible!==false})):EMPTY;
  const update=(patch:Partial<Preferences>)=>setPreferences(previous=>{const next={...previous,...patch};localStorage.setItem(storageKey+'.v7',JSON.stringify(next));return next;});
  const change=(key:Category|'transition',patch:{hidden?:boolean;color?:string})=>update({[key]:{...preferences[key],...patch}});
  const reset=()=>{localStorage.removeItem(storageKey+'.v7');setPreferences({});};
  const status=book?'ready':error?'unavailable':'loading';
  const latest=[...new Map(book?.segments.filter(s=>s.valid_to===book.as_of).map(s=>[s.id,s])??[]).values()];
  const editor=<div className="v7-legend-content" data-book-status={status}>
    <label className="legend-checkbox"><input type="checkbox" checked={visible} onChange={e=>update({visible:e.target.checked})}/>Show level book</label>
    <div className="chart-legend-editor-section-title">Visibility &amp; colors</div>
    {([...Object.entries(categories),['transition','Role transitions']] as [Category|'transition',string][]).map(([key,label])=><div className="v7-category-control" key={key}>
      <label className="legend-checkbox"><input aria-label={`Show ${label.toLowerCase()}`} type="checkbox" checked={key==='transition'?!transitionsHidden:!preferences[key]?.hidden} onChange={e=>change(key,{hidden:!e.target.checked})}/>{label}</label>
      <input type="color" aria-label={`${label} color`} value={preferences[key]?.color || getComputedStyle(document.documentElement).getPropertyValue(key==='transition'?'--muted-foreground':`--chart-v6-${key}`).trim()} onChange={e=>change(key,{color:e.target.value})}/>
    </div>)}
    <label className="legend-checkbox"><input type="checkbox" checked={preferences.bandsVisible!==false} onChange={e=>update({bandsVisible:e.target.checked})}/>Show band fills</label>
    <label className="legend-checkbox"><input type="checkbox" checked={preferences.centersVisible!==false} onChange={e=>update({centersVisible:e.target.checked})}/>Show center lines</label>
    <small>Solid: historical. Dashed: current day. Transitions await confirmation of a support or resistance role.</small>
    <div className="chart-legend-editor-section-title">Display thresholds</div>
    <label className="legend-filter-control">
      <span className="legend-filter-control-copy"><span>Minimum reactions</span><small>Independent observations used to fit each band.</small></span>
      <span className="legend-range-control"><input aria-label="Minimum reactions" type="range" min={0} max={Math.max(100,...latest.map(s=>s.fit.count))} step={1} value={minimumReactions} onChange={e=>update({minimumReactions:Number(e.target.value)})}/><output data-numeric="true">{minimumReactions===0?'Any':minimumReactions}</output></span>
    </label>
    <label className="legend-filter-control">
      <span className="legend-filter-control-copy"><span>Maximum band width</span><small>Full band width divided by its center, in basis points. 100 bps = 1%.</small></span>
      <span className="legend-range-control"><input aria-label="Maximum band width (bps)" type="range" min={0} max={1000} step={1} value={maximumWidthBps} onChange={e=>update({maximumWidthBps:Number(e.target.value)})}/><output data-numeric="true">{maximumWidthBps===0?'No limit':`${maximumWidthBps} bps`}</output></span>
    </label>
    <small>{latest.filter(passesThresholds).length} of {latest.length} current-session bands pass the thresholds. Filters affect chart display only, including prior-day bands.</small>
    <small>V7 has no level-strength score or calibrated reaction probability. MLE coverage is not a bounce probability.</small>
    <details><summary>Streaming discovery</summary>
    {book?<><dl className="v7-book-counts"><dt>Historical bands</dt><dd>{book.historical_count}</dd><dt>Current-day bands</dt><dd>{book.current_day_count}</dd><dt>Candidates awaiting fit</dt><dd>{book.candidate_count}</dd><dt>Confirmed turning points</dt><dd>{book.proposals}</dd><dt>Matched existing levels</dt><dd>{book.merged_proposals}</dd></dl>
      <small>Historical merges remain historical. New bands require at least three independent reactions. Candidates include unqualified levels carried from the prior session.</small>
    </>:<p role={error?'alert':'status'}>{error || 'Loading causal level book...'}</p>}
</details>
    {priorView && <div data-prior-book-status={priorView.error?'unavailable':priorView.loading?'loading':'ready'}>
      <p>{priorView.count} prior-session checkpoints displayed{priorView.loading?' - loading...':''}.</p>
      {priorView.error && <p role="alert">{priorView.error}</p>}
      <small>Earlier days use their saved end-of-session geometry, for chart review only. Current-session and strategy inputs remain causal.</small>
    </div>}
    {book && <details><summary>Checkpoint &amp; timing</summary><p>As of {new Date(book.as_of*1000).toLocaleTimeString('en-GB',{timeZone:'America/New_York'})} ET. MLE updates use completed 1s candles, independent of chart timeframe.</p><p>{book.book_version}</p><p data-technical-identifier="true">{book.book_hash}</p></details>}
  </div>;
  return {segments,priorStatus:priorView?(priorView.error?'unavailable':priorView.loading?'loading':'ready'):undefined,end:Number.isFinite(cutoff)?cutoff:0,visible,status,editor,reset,setVisible:(visible:boolean)=>update({visible}),
    summary:book?`${book.historical_count} historical / ${book.current_day_count} day`:status};
}

export class ReactionBookPrimitive implements ISeriesPrimitive<Time>{
  private series?:ISeriesApi<'Candlestick'>;private update?:()=>void;private segments:Segment[]=EMPTY;
  private coordinate:(t:number)=>number|null=()=>null;private end=0;
  private view:IPrimitivePaneView={zOrder:()=> 'bottom',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
    if(!this.series)return;
    const theme=getComputedStyle(document.documentElement);
    ctx.save();
    for(const s of this.segments){
      if(s.valid_from>=this.end)continue;
      const x1=this.coordinate(s.valid_from),x2=this.coordinate(Math.min(s.valid_to,this.end));
      const hi=this.series.priceToCoordinate(s.upper),lo=this.series.priceToCoordinate(s.lower),y=this.series.priceToCoordinate(s.price);
      if(x1===null || x2===null || hi===null || lo===null || y===null || lo<0 || hi>mediaSize.height)continue;
      ctx.strokeStyle=ctx.fillStyle=s.color || theme.getPropertyValue(s.category==='transition'?'--muted-foreground':`--chart-v6-${s.category}`).trim();
      ctx.globalAlpha=s.historical?.10:.06;if(s.showBand!==false)ctx.fillRect(x1,hi,x2-x1,lo-hi);
      ctx.globalAlpha=s.historical?.7:.45;ctx.lineWidth=s.historical?1.5:1;ctx.setLineDash(s.historical?[]:[4,4]);
      if(s.showCenter!==false){ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();}
    }
    ctx.restore();
  })})};
  attached({series,requestUpdate}:Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]){this.series=series as ISeriesApi<'Candlestick'>;this.update=requestUpdate;}
  detached(){this.series=undefined;this.update=undefined;}
  paneViews(){return [this.view];}
  setState(segments:Segment[],coordinate:(t:number)=>number|null,end:number){this.segments=segments;this.coordinate=coordinate;this.end=end;this.update?.();}
}
