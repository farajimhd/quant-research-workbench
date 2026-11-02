"""Causal batch windows over referenced bars, without duplicated sequences.

This is a new dataset API, not a silent replacement for V64's scalar catalog.
Market arrays are allocated only for the requested batch and active ticker set.
"""
import json
from collections import OrderedDict
from pathlib import Path
import numpy as np
import polars as pl
from .contract import TRADE_WINDOWS, QUOTE_WINDOWS, origin_us, sha
from .source import price_factor

TRADE_FIELDS=('open','high','low','close','volume','trade_count','notional')
QUOTE_FIELDS=('open','high','low','close','close_size','event_count')


def scheduled_clocks(day, clocks, resolution, count, prior_days):
    """Return scheduled ends, not last N observed bars (which hide gaps)."""
    clocks=np.asarray(clocks,dtype=np.int64)
    origin=origin_us(day);step=resolution*1000
    if resolution==3600000:
        schedule=np.concatenate([origin_us(d)+np.arange(5,21,dtype=np.int64)*3600_000000 for d in prior_days+[day]])
        end=np.searchsorted(schedule,clocks,side='right')
        index=end[:,None]-count+np.arange(count)[None,:]
        valid=index>=0
        return schedule[np.maximum(index,0)],valid
    end=(clocks-origin)//step*step+origin
    targets=end[:,None]-(count-1-np.arange(count))[None,:]*step
    valid=(targets>origin+14400_000000)&(targets<=origin+34200_000000)
    return targets,valid


class Session:
    def __init__(self, manifest, day, cache_bytes=512*2**20):
        manifest=Path(manifest);self.root=manifest.parent
        self.manifest=json.loads(manifest.read_text())
        self.item=next(r for r in self.manifest['sessions'] if r['day']==day)
        self.day=day;self.source=json.loads((self.root/'source.json').read_text())
        if self.source['hash']!=self.manifest['source_hash']:
            raise ValueError('Manifest/source identity mismatch')
        self.verified=set();self.verified_files=set();self.seals={}
        if cache_bytes<0:raise ValueError('Nonnegative host cache budget required')
        self.cache=OrderedDict();self.cache_bytes=cache_bytes;self.cache_used=0

    def _read(self,day,name,tickers):
        folder=self.root/'bars'/day;seal_path=folder/'complete.json'
        if day not in self.verified:
            if sha(seal_path)!=self.manifest['shard_seals'][day]:
                raise ValueError('Shard seal changed')
            seal=json.loads(seal_path.read_text())
            self.seals[day]=seal
            self.verified.add(day)
        path=folder/(name+'.parquet')
        if not path.exists():
            if path.name in self.seals[day]['files']:raise ValueError('Sealed shard missing: '+path.name)
            return None
        key=(day,name)
        if key not in self.verified_files:
            if sha(path)!=self.seals[day]['files'][path.name]:raise ValueError('Shard bytes changed: '+path.name)
            self.verified_files.add(key)
        if key in self.cache:
            frame=self.cache.pop(key);self.cache[key]=frame
            return frame.filter(pl.col('ticker').is_in(list(tickers)))
        if name.startswith('execution-') or not self.cache_bytes:
            return pl.scan_parquet(path).filter(pl.col('ticker').is_in(list(tickers))).collect()
        frame=pl.read_parquet(path);size=frame.estimated_size()
        if size<=self.cache_bytes:
            while self.cache and self.cache_used+size>self.cache_bytes:
                _,old=self.cache.popitem(last=False);self.cache_used-=old.estimated_size()
            self.cache[key]=frame;self.cache_used+=size
        return frame.filter(pl.col('ticker').is_in(list(tickers)))

    def selection(self,clocks):
        """The exact causal top five; full-session storage union is not admission."""
        clocks=np.asarray(clocks,dtype=np.int64)
        if clocks.ndim!=1:raise ValueError('One-dimensional clocks required')
        if not hasattr(self,'rank_arrays'):
            folder=self.root/'scanner'/self.day
            seal=json.loads((folder/'complete.json').read_text())
            if seal['source_hash']!=self.source['hash'] or sha(folder/'rank.parquet')!=seal['files']['rank.parquet']:
                raise ValueError('Scanner seal/bytes changed')
            rank=pl.read_parquet(folder/'rank.parquet')
            index=((rank['time_us'].to_numpy()-origin_us(self.day))//1000000-14401).astype(np.int64)
            columns=rank['rank'].to_numpy().astype(np.int64)-1
            names=np.full((19800,5),'',dtype=object);counts=np.zeros((19800,5),dtype=np.uint64)
            names[index,columns]=rank['ticker'].to_numpy();counts[index,columns]=rank['trade_count_60s'].to_numpy()
            self.rank_arrays=(names,counts)
        names,counts=self.rank_arrays
        row=(clocks-origin_us(self.day))//1000000-14401
        if ((row<0)|(row>=19800)|(clocks%1000000!=0)).any():raise ValueError('Decision clock outside scanner grid')
        return dict(tickers=names[row],trade_count_60s=counts[row],valid=counts[row]>0)

    def windows(self,tickers,clocks,resolution=1000,side=None):
        tickers=np.asarray(tickers);clocks=np.asarray(clocks,dtype=np.int64)
        if tickers.ndim!=1 or clocks.shape!=tickers.shape or not set(tickers)<=set(self.item['selected']):
            raise ValueError('Batch clocks/tickers must match the session union')
        origin=origin_us(self.day)
        if ((clocks<=origin+14400_000000)|(clocks>origin+34200_000000)).any():
            raise ValueError('Model access outside decision interval')
        if side not in (None,'bid','ask'):raise ValueError('Unknown quote side')
        windows=QUOTE_WINDOWS if side else TRADE_WINDOWS
        if resolution not in windows:raise ValueError('Unsupported context resolution')
        count=windows[resolution];fields=QUOTE_FIELDS if side else TRADE_FIELDS
        values=np.zeros((len(tickers),count,len(fields)),dtype=np.float32)
        valid=np.zeros_like(values,dtype=bool)
        unique=sorted(set(tickers))
        daily=resolution==86400000
        days=self.item['daily'] if daily else self.item['hourly'] if resolution==3600000 else [self.day]
        if daily:
            target=np.broadcast_to(np.arange(-count,0,dtype=np.int64),(len(tickers),count)).copy()
            schedule_valid=target>=-len(days)
        else:
            target,schedule_valid=scheduled_clocks(self.day,clocks,resolution,count,[d for d in days if d!=self.day])
        pieces=[]
        for d in days:
            frame=self._read(d,('quote-' if side else 'trade-')+str(resolution),unique)
            if frame is None or not frame.height:continue
            if side:frame=frame.filter(pl.col('side')==side)
            frame=frame.with_columns(pl.lit(d).alias('source_day'))
            if daily:frame=frame.with_columns(pl.lit(days.index(d)-len(days)).cast(pl.Int64).alias('lookup'))
            else:frame=frame.with_columns(pl.col('time_us').alias('lookup'))
            pieces.append(frame)
        if not pieces:return dict(values=values,valid=valid,clocks=target,fields=fields)
        frame=pl.concat(pieces,how='vertical_relaxed')
        # Metadata loops are per ticker/session, never per market row or clock.
        for ticker in unique:
            rows=frame.filter(pl.col('ticker')==ticker).sort('lookup')
            if not rows.height:continue
            batch=np.flatnonzero(tickers==ticker);wanted=target[batch]
            observed=rows['lookup'].to_numpy();ix=np.searchsorted(observed,wanted)
            bounded=np.minimum(ix,len(observed)-1)
            found=(ix<len(observed))&(observed[bounded]==wanted)&schedule_valid[batch]
            availability=rows['available_at_us' if daily else 'time_us'].to_numpy()
            found &= availability[bounded]<=clocks[batch,None]
            raw=rows.select(fields).to_numpy().astype(np.float64)
            masks=np.ones(raw.shape,dtype=bool)
            if side:
                masks[:,:4]=raw[:,:4]>0
            else:
                masks[:,[0,3]]=rows['price_valid'].to_numpy()[:,None]==1
                masks[:,[1,2]]=rows['extremes_valid'].to_numpy()[:,None]==1
            if days!=[self.day]:
                factors={d:price_factor(self.source['splits'],ticker,d,self.day) for d in days if d<self.day}
                factor=np.array([factors.get(d,1.) for d in rows['source_day']],dtype=np.float64)
                raw[:,:4]*=factor[:,None]
                if not side:raw[:,4]/=factor
            selected=raw[bounded];mask=found[:,:,None]&masks[bounded]&np.isfinite(selected)
            values[batch]=np.where(mask,selected,0).astype(np.float32);valid[batch]=mask
        return dict(values=values,valid=valid,clocks=target,fields=fields)

    def execution_pages(self):
        """Execution-only iterator; tail is deliberately outside windows()."""
        for begin in range(14400,72000,3600):
            yield self._read(self.day,f'execution-{begin}',self.item['selected'])
