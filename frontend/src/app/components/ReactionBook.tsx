import {useEffect,useRef,useState} from 'react';
import type {IPrimitivePaneView,ISeriesApi,ISeriesPrimitive,Time} from 'lightweight-charts';

type Segment={id:string;price:number;lower:number;upper:number;role:string;historical:boolean;valid_from:number;valid_to:number;model_input:boolean};
type Book={as_of:number;model_id?:string;book_id?:string;book_hash:string;book_version:string;segments:Segment[];historical_count:number;current_day_count:number};
const EMPTY:Segment[]=[];

function placeSettings(details:HTMLDetailsElement|null){
  const panel=details?.querySelector<HTMLElement>(':scope > div');
  if(!details?.open || !panel)return;
  const anchor=details.getBoundingClientRect(),width=panel.getBoundingClientRect().width;
  panel.style.left=`${Math.max(8-anchor.left,Math.min(0,window.innerWidth-anchor.left-width-8))}px`;
  panel.style.right='auto';
}

export function useReactionBook(ticker:string,day?:string,asOf?:string,selectedModel?:string){
  const [enabled,setEnabled]=useState(true);
  const settingsRef=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{const align=()=>placeSettings(settingsRef.current);window.addEventListener('resize',align);return()=>window.removeEventListener('resize',align);},[]);
  const [historical,setHistorical]=useState(true),[current,setCurrent]=useState(true);
  const [automatic,setAutomatic]=useState<{key:string;id:string}>();
  const [v7,setV7]=useState<{key:string;id:string}>();
  const [source,setSource]=useState('auto');
  const [value,setValue]=useState<{key:string;book:Book}>();
  const [error,setError]=useState('');
  const identity=JSON.stringify([ticker,day]);
  const model=selectedModel || (automatic?.key===identity?automatic.id:undefined);
  const v7Id=v7?.key===identity?v7.id:undefined;
  const useV7=source==='v7' || (source==='auto' && !!v7Id);
  const selected=useV7?v7Id:model;
  const key=JSON.stringify([identity,selected,useV7]);
  const cutoff=asOf?Date.parse(asOf)/1000:NaN;
  const desired=useRef(cutoff);desired.current=cutoff;
  useEffect(()=>{
    if(!enabled || !day)return;
    const controller=new AbortController();
    fetch('/api/research/level-book-v7/catalog',{signal:controller.signal}).then(async r=>{
      if(!r.ok)throw new Error('V7 catalog unavailable');
      const rows:{id:string;ticker:string;ready:boolean;dates:string[]}[]=await r.json();
      const match=rows.filter(m=>m.ready && m.ticker===ticker && m.dates.includes(day)).at(-1);
      if(!controller.signal.aborted)setV7(match?{key:identity,id:match.id}:undefined);
    }).catch(()=>{if(!controller.signal.aborted)setV7(undefined);});
    fetch('/api/research/level-reaction/models',{signal:controller.signal}).then(async r=>{
      if(!r.ok)throw new Error('Reaction book catalog unavailable');
      const rows: {id:string;ticker:string;ready:boolean;dates:string[]}[]=await r.json();
      const match=rows.filter(m=>m.ready && m.ticker===ticker && m.dates.includes(day)).at(-1);
      if(!controller.signal.aborted){setAutomatic(match?{key:identity,id:match.id}:undefined);setError(match?'':'No prepared reaction book for this session');}
    }).catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return()=>controller.abort();
  },[enabled,identity]);
  useEffect(()=>{
    if(!enabled || !day || !selected)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;let completed=NaN;
    const run=async()=>{
      const stamp=Math.floor(desired.current);
      try{
        if(Number.isFinite(stamp) && stamp!==completed){
          const date=new Date(stamp*1000);
          if(date.toLocaleDateString('en-CA',{timeZone:'America/New_York'})!==day)throw new Error('Chart cursor and book session do not match');
          const response=await fetch(useV7?'/api/research/level-book-v7/book':'/api/research/level-reaction/book',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({...(useV7?{book_id:selected}:{model_id:selected}),ticker,session_date:day,time_et:date.toLocaleTimeString('en-GB',{timeZone:'America/New_York',hour12:false})})});
          if(response.status!==429){const book=await response.json();if(!response.ok)throw new Error(book.detail || 'Reaction book unavailable');
            if(!controller.signal.aborted){setValue({key,book});setError('');completed=stamp;}}
        }
      }catch(e){if(!controller.signal.aborted){setError(String(e));setValue(undefined);}}
      if(!controller.signal.aborted)timer=setTimeout(()=>void run(),1000);
    };
    void run();return()=>{controller.abort();if(timer)clearTimeout(timer);};
  },[enabled,key]);
  // A rewind must not expose later role changes/reinforcements while reloading.
  const book=enabled && value?.key===key && value.book.as_of<=cutoff?value.book:undefined;
  const segments=book?.segments.filter(s=>s.historical?historical:current)??EMPTY;
  return {segments,end:Math.min(book?.as_of??0,cutoff),controls:day?<span className="reaction-book-controls">
    <label className="toolbar-button"><input type="checkbox" aria-label="Show reaction book" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Reaction book</label>
    {enabled && <details ref={settingsRef} onToggle={()=>placeSettings(settingsRef.current)} className="reaction-book-settings"><summary title={error || 'Historical bands and causal current-day levels'}>{book?`${useV7?'V7':'Model'} · ${book.historical_count} historical · ${book.current_day_count} day`:error?'Unavailable':'Loading…'}</summary>
      <div><label>Book source<select className="level-reaction-model" aria-label="Level book source" value={source} onChange={e=>setSource(e.target.value)}><option value="auto">Automatic (prefer V7)</option><option value="v7">V7 historical + streaming</option><option value="model">Prediction model book</option></select></label>
        {useV7 && !v7Id && <p role="alert">No prepared V7 book for this session.</p>}
        <label><input type="checkbox" checked={historical} onChange={e=>setHistorical(e.target.checked)}/>Historical book</label>
        <label><input type="checkbox" checked={current} onChange={e=>setCurrent(e.target.checked)}/>{useV7?'Current-day confirmed levels':'Current-day swing observations'}</label>
        <p>Solid: historical. Dashed: current day. Neutral: role in transition. Historical merges keep historical identity. {useV7?'V7 updates after completed 1s candles. Prediction labels still use their separately frozen model book.':'Predictions use the historical model book; current-day swings are context.'}</p>
        {error && <p role="alert">{error}</p>}{book && <p>Version {book.book_version}<br/>Book hash {book.book_hash}</p>}</div>
    </details>}
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
      ctx.strokeStyle=ctx.fillStyle=theme.getPropertyValue(s.role==='support'?'--success':s.role==='resistance'?'--danger':'--muted-foreground').trim();
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
