/** Causal EMA curvature from completed chart candles; no forecast inputs. */
export const EMA_ACCELERATION_ID='indicator.ema_acceleration';
export const EMA_ACCELERATION_KEY=`oscillator:${EMA_ACCELERATION_ID}:ema_acceleration`;
export const accelerationUnits={ 'price-bar2':'Price / candle²', 'bps-bar2':'bps / candle²', 'price-second2':'Price / second²', 'bps-second2':'bps / second²' };
export type AccelerationUnit=keyof typeof accelerationUnits;
export function emaPeriod(value:unknown){const n=Number(value);return Number.isFinite(n)?Math.max(1,Math.min(500,Math.round(n))):7;}
export function accelerationUnit(value:unknown):AccelerationUnit{return typeof value==='string' && value in accelerationUnits?value as AccelerationUnit:'price-bar2';}
export function emaAcceleration(candles:Array<{time:number;endTime?:number;isClosed?:boolean;close:number}>,length:number,unit:AccelerationUnit,asOf:number,duration:number){
  const period=emaPeriod(length),alpha=2/(period+1),points:Array<{time:number;value:number}>=[];
  let sum=0,count=0,ema:number|undefined;let samples:Array<{value:number;t:number}>=[];
  for(const candle of candles){
    const end=candle.endTime??candle.time+duration;
    if(candle.isClosed===false || !Number.isFinite(end) || end>asOf)continue;
    if(!Number.isFinite(candle.close) || candle.close<=0){sum=0;count=0;ema=undefined;samples=[];continue;}
    if(ema===undefined){sum+=candle.close;count++;if(count<period)continue;ema=sum/period;}
    else ema+=alpha*(candle.close-ema);
    samples.push({value:ema,t:end});if(samples.length<3)continue;
    if(samples.length>3)samples.shift();
    const [a,b,c]=samples;let value=c.value-2*b.value+a.value;
    if(unit.endsWith('second2')){
      const previous=b.t-a.t,current=c.t-b.t;
      if(previous<=0 || current<=0)continue;
      value=2*((c.value-b.value)/current-(b.value-a.value)/previous)/(current+previous);
    }
    if(unit.startsWith('bps'))value=value/ema*10000;
    if(Number.isFinite(value))points.push({time:candle.time,value});
  }
  return points;
}
