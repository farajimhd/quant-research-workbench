import {useEffect,useRef,useState} from 'react';
import type {IPrimitivePaneView,ISeriesApi,ISeriesPrimitive,Time} from 'lightweight-charts';

type Segment={id:string;price:number;lower:number;upper:number;role:string;historical:boolean;valid_from:number;valid_to:number;model_input:boolean;color?:string;category?:string};
type Book={as_of:number;model_id?:string;book_id?:string;book_hash:string;book_version:string;segments:Segment[];historical_count:number;current_day_count:number};
const EMPTY:Segment[]=[];

function placeSettings(details:HTMLDetailsElement|null){
  const panel=details?.querySelector<HTMLElement>(':scope > div');
  if(!details?.open || !panel)return;
  const anchor=details.getBoundingClientRect(),width=panel.getBoundingClientRect().width;
  panel.style.left=`${Math.max(8-anchor.left,Math.min(0,window.innerWidth-anchor.left-width-8))}px`;
  panel.style.right='auto';
}

type Category='historicalResistance'|'currentResistance'|'historicalSupport'|'currentSupport';
const categories:Record<Category,string>={historicalResistance:'Historical resistance',currentResistance:'Current-day resistance',historicalSupport:'Historical support',currentSupport:'Current-day support'};
type Preferences=Partial<Record<Category,{hidden?:boolean;color?:string}>> & {transitionsHidden?:boolean};
export function useReactionBook(ticker:string,day:string|undefined,asOf:string|undefined,enabled:boolean,mode:'history'|'live',storageKey:string){
  const settingsRef=useRef<HTMLDetailsElement>(null);
  const [preferences,setPreferences]=useState<Preferences>(()=>{try{return JSON.parse(localStorage.getItem(storageKey+'.v7')||'{}');}catch{return {};}});
  const [value,setValue]=useState<{key:string;book:Book}>();
  const [error,setError]=useState('');
  const key=JSON.stringify([ticker,day,mode]);
  const cutoff=asOf?Date.parse(asOf)/1000:mode==='live'?Date.now()/1000:NaN;
  const desired=useRef(cutoff);desired.current=cutoff;
  useEffect(()=>{const align=()=>placeSettings(settingsRef.current);window.addEventListener('resize',align);return()=>window.removeEventListener('resize',align);},[]);
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
  const segments=book?.segments.filter(s=>s.role==='transition'?!preferences.transitionsHidden:!preferences[category(s)]?.hidden).map(s=>({...s,color:s.role==='transition'?undefined:preferences[category(s)]?.color,category:s.role==='transition'?'transition':category(s)}))??EMPTY;
  const change=(key:Category,patch:{hidden?:boolean;color?:string})=>setPreferences(previous=>{const next={...previous,[key]:{...previous[key],...patch}};localStorage.setItem(storageKey+'.v7',JSON.stringify(next));return next;});
  return {segments,end:Math.min(book?.as_of??0,cutoff),controls:enabled?<span className="reaction-book-controls">
    <details ref={settingsRef} onToggle={()=>placeSettings(settingsRef.current)} className="reaction-book-settings" data-book-status={book?'ready':error?'unavailable':'loading'}><summary className="toolbar-button" title={error || 'QMD historical + causal streaming MLE bands'}>{book?'V7 settings':error?'V7 unavailable':'V7 loading...'}</summary>
      <div>{book && <p>{book.historical_count} historical levels; {book.current_day_count} current-day levels</p>}{(Object.keys(categories) as Category[]).map(key=><label key={key}><input type="checkbox" checked={!preferences[key]?.hidden} onChange={e=>change(key,{hidden:!e.target.checked})}/>{categories[key]}<input type="color" aria-label={`${categories[key]} color`} value={preferences[key]?.color || getComputedStyle(document.documentElement).getPropertyValue(`--chart-v6-${key}`).trim()} onChange={e=>change(key,{color:e.target.value})}/></label>)}
        <p>Solid: historical. Dashed: current day. Merged historical levels retain historical identity. Bands refit from confirmed reactions on completed 1s candles; changing chart timeframe does not change the book.</p>
        <label><input type="checkbox" checked={!preferences.transitionsHidden} onChange={e=>setPreferences(previous=>{const next={...previous,transitionsHidden:!e.target.checked};localStorage.setItem(storageKey+'.v7',JSON.stringify(next));return next;})}/>Role transitions (gray)</label>
        <p>Gray bands await confirmation of a support or resistance role.</p>
        {error && <p role="alert">{error}</p>}{book && <p>As of {new Date(book.as_of*1000).toLocaleTimeString('en-GB',{timeZone:'America/New_York'})} ET<br/>Version {book.book_version}<br/>Checkpoint {book.book_hash}</p>}</div>
    </details>
  </span>:null};
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
      ctx.globalAlpha=s.historical?.10:.06;ctx.fillRect(x1,hi,x2-x1,lo-hi);
      ctx.globalAlpha=s.historical?.7:.45;ctx.lineWidth=s.historical?1.5:1;ctx.setLineDash(s.historical?[]:[4,4]);
      ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();
    }
    ctx.restore();
  })})};
  attached({series,requestUpdate}:Parameters<NonNullable<ISeriesPrimitive<Time>['attached']>>[0]){this.series=series as ISeriesApi<'Candlestick'>;this.update=requestUpdate;}
  detached(){this.series=undefined;this.update=undefined;}
  paneViews(){return [this.view];}
  setState(segments:Segment[],coordinate:(t:number)=>number|null,end:number){this.segments=segments;this.coordinate=coordinate;this.end=end;this.update?.();}
}
