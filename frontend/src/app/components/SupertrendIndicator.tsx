import {useEffect,useMemo,useState} from 'react';
import {LineSeries,type IChartApi,type ISeriesApi,type Time} from 'lightweight-charts';
import {Modal} from './Modal';
import {supertrend,type SupertrendCandle,type SupertrendPoint} from './supertrend';
import './supertrend.css';

const defaults={enabled:true,period:10,multiplier:3};
export function useSupertrend(storageKey:string,timeframe:string,candles:SupertrendCandle[],asOf?:string) {
  const [stored,setStored]=useState({...defaults,key:storageKey});
  const [open,setOpen]=useState(false);
  useEffect(()=>{
    try {
      const s=JSON.parse(localStorage.getItem(storageKey+'.supertrend') || '{}');
      setStored({key:storageKey,enabled:typeof s.enabled==='boolean' ? s.enabled : true,
        period:Number.isInteger(s.period) && s.period>=1 && s.period<=500 ? s.period : 10,
        multiplier:Number.isFinite(s.multiplier) && s.multiplier>0 && s.multiplier<=100 ? s.multiplier : 3});
    } catch {setStored({...defaults,key:storageKey});}
  },[storageKey]);
  const settings=stored.key===storageKey ? stored : {...defaults,key:storageKey};
  const change=(patch:Partial<typeof defaults>)=>{
    const next={...settings,...patch};setStored(next);
    try {localStorage.setItem(storageKey+'.supertrend',JSON.stringify(next));} catch { /* Current chart still works without storage. */ }
  };
  const enabled=settings.enabled && timeframe==='1s';
  const parsed=asOf ? Date.parse(asOf)/1000 : Infinity;
  const tail=candles.at(-1);
  // Stable for historical charts: pan/hover renders must not recalculate ATR.
  const cutoff=Math.min(Math.floor(Date.now()/1000),Number.isFinite(parsed) ? parsed : Infinity,
    tail ? tail.endTime ?? tail.time+1 : Infinity);
  const result=useMemo(()=>{
    if (!enabled) return {points:[] as SupertrendPoint[],error:''};
    try {return {points:supertrend(candles,settings.period,settings.multiplier,cutoff),error:''};}
    catch(e) {return {points:[] as SupertrendPoint[],error:String(e)};}
  },[enabled,candles,settings.period,settings.multiplier,cutoff]);
  const last=result.points.at(-1);
  return {enabled,points:result.points,
    checkbox:<label className="chart-setting-row"><span>Supertrend <small>1s price overlay</small></span><input type="checkbox" aria-label="Supertrend" disabled={timeframe!=='1s'} checked={enabled} onChange={e=>change({enabled:e.target.checked})}/></label>,
    controls:<>{timeframe==='1s' && <label className="toolbar-button supertrend-toolbar"><input type="checkbox" aria-label="Show 1s Supertrend" checked={enabled} onChange={e=>change({enabled:e.target.checked})}/>Supertrend</label>}{enabled && <button type="button" className="toolbar-button supertrend-toolbar" onClick={()=>setOpen(true)} title="Supertrend settings: completed candles only">Supertrend {settings.period} × {settings.multiplier} · {result.error ? 'Unavailable' : last ? last.direction===1 ? 'Up' : 'Down' : 'Warming up'}</button>}
      {open && <Modal title="Supertrend settings" onClose={()=>setOpen(false)}><div className="supertrend-settings">
        <label className="chart-setting-row"><span>Show 1s Supertrend</span><input type="checkbox" aria-label="Supertrend visibility" checked={settings.enabled} onChange={e=>change({enabled:e.target.checked})}/></label>
        <label className="chart-setting-row"><span>ATR period (candles)</span><input aria-label="Supertrend ATR period" type="number" min={1} max={500} step={1} value={settings.period} onChange={e=>{const n=Number(e.target.value);if(Number.isInteger(n)&&n>=1&&n<=500)change({period:n});}}/></label>
        <label className="chart-setting-row"><span>ATR multiplier</span><input aria-label="Supertrend multiplier" type="number" min={0.1} max={100} step={0.1} value={settings.multiplier} onChange={e=>{const n=Number(e.target.value);if(Number.isFinite(n)&&n>0&&n<=100)change({multiplier:n});}}/></label>
        <p>Green follows an uptrend below price; red follows a downtrend above price. Uses Wilder ATR and completed 1-second candles. The line is plotted on its source candle and becomes known at that candle’s close.</p>
        <p>Warmup needs {settings.period} completed candles. Calculation starts at the loaded history; loading earlier history can change the ATR seed. Missing seconds add no synthetic candles. This overlay does not change detector or strategy signals.</p>
        {result.error && <p role="alert">{result.error}</p>}
      </div></Modal>}</>};
}

export class SupertrendRenderer {
  private line:ISeriesApi<'Line'>;
  constructor(private chart:IChartApi) {
    this.line=chart.addSeries(LineSeries,{lineWidth:2,priceLineVisible:false,crosshairMarkerVisible:false,title:'Supertrend'});
  }
  update(points:SupertrendPoint[],upColor:string,downColor:string) {
    const last=points.at(-1);
    this.line.applyOptions({color:last?.direction===1 ? upColor : downColor,lastValueVisible:!!last});
    this.line.setData(supertrendLineData(points,upColor,downColor));
  }
  remove() {this.chart.removeSeries(this.line);}
}

export function supertrendLineData(points:SupertrendPoint[],upColor:string,downColor:string) {
  // Lightweight Charts colors the segment *after* each point and bridges
  // whitespace. Hide only a flip's connecting segment, using one native series.
  return points.map((p,i)=>({time:p.time as Time,value:p.value,
    color:points[i+1] && points[i+1].direction!==p.direction ? 'transparent' : p.direction===1 ? upColor : downColor}));
}
