"""Sparse causal feature points and full execution grid over shared ARTE shards."""
import json
import time
from pathlib import Path
import numpy as np
import polars as pl
import torch
from research.market_shards.v1.data import Session
from research.market_shards.v1.contract import origin_us, sha
from .source_data import SessionData as Lifecycle
from .features import BASE, HISTORY, VARIANTS
from .enriched_math import NAMES, OWN_NAMES, MARKET_HORIZONS, market_summary
from .certified_history_bank import calculate as history_values
from .shard_math import scalar_features, rolling_sum
from .shard_features import materialize


class SessionData(Lifecycle):
    def __init__(self, root, history=None, *, device='cpu', maximum_gib=4.,
                 manifest=None, feature_ticks=None, **kwargs):
        start=time.perf_counter();root=Path(root)
        if manifest is None:
            raise ValueError('V65 requires an explicit sealed shared-shard manifest')
        session=Session(manifest,root.name);self.shard_session=session
        self.root=root;self.device=torch.device('cpu');self.feature_cache=None
        self.tickers=tuple(sorted(session.item['selected']))
        # Identity labels are shared between both manifests, never model features.
        names=set()
        for role in ('training','validation'):
            doc=json.loads((session.root/(role+'.json')).read_bytes())
            names.update(t for s in doc['sessions'] for t in s['selected'])
        labels={t:i+1 for i,t in enumerate(sorted(names))}
        self.listing_ids=np.array([labels[t] for t in self.tickers],np.int64)
        origin=origin_us(session.day)//1000000
        clocks=origin+np.arange(14401,34201,dtype=np.int64);self.clocks=len(clocks)
        n,u=len(clocks),len(self.tickers)
        selection=session.selection(clocks*1000000)
        columns={t:i for i,t in enumerate(self.tickers)}
        slots=np.full((n,5),-1,np.int64)
        for ticker,col in columns.items():slots[selection['tickers']==ticker]=col
        top=np.where(slots>=0,self.listing_ids[slots.clip(0)],-1)
        membership=np.zeros((n,u),bool)
        tick,rank=np.nonzero(slots>=0);membership[tick,slots[tick,rank]]=True
        needed=membership.copy()
        if feature_ticks is not None:
            ticks=np.asarray(feature_ticks,dtype=np.int64)
            if ((ticks<0)|(ticks>=n)).any():raise ValueError('Calibration tick outside session')
            # Rank-change features also need the top-five universe 15 seconds ago.
            context_ticks=np.unique(np.r_[ticks,np.maximum(0,ticks-15)])
            requested=np.zeros(n,bool);requested[context_ticks]=True
            needed &= requested[:,None]
        # The lag input uses today's selected ticker at the preceding clock.
        needed[:-1]|=needed[1:].copy()
        if feature_ticks is not None:
            prior=np.zeros(n,bool);prior[np.maximum(0,context_ticks-1)]=True
            needed |= membership & prior[:,None]
        times,cols=np.nonzero(needed);p=len(times)
        if not p:raise ValueError('Empty scanner session')
        source=np.full((n,u),-1,np.int32);source[times,cols]=np.arange(p,dtype=np.int32)
        self.arrays=dict(clocks=clocks,top_indices=top,market_keys=clocks[times],
            features=np.zeros((p,len(BASE)),np.float32),feature_valid=np.zeros((p,len(BASE)),bool))
        self.bank=dict(source_rows=source,ids=source.clip(0),values=np.full((p,len(HISTORY)),np.nan,np.float32))
        self.receipt=dict(validation_opened=Path(manifest).stem=='validation',
            listing_ids=self.listing_ids.tolist(),tickers=list(self.tickers))
        self.identity=dict(input_sha256=sha(manifest),history_sha256=session.source['hash'],
            shard_day=session.day,scanner_sha256=sha(session.root/'scanner'/session.day/'complete.json'),
            execution_tail_sha256=session.manifest['shard_seals'][session.day])
        if feature_ticks is not None:self.identity['calibration_ticks']=ticks.tolist()
        trade=session._read(session.day,'trade-1000',self.tickers)
        if trade is None:raise ValueError('Required one-second trade shard missing')
        previous_days=[d for d in session.item['hourly'] if d<session.day]
        previous_day=max(previous_days) if previous_days else None
        previous=session._read(previous_day,'trade-1000',self.tickers) if previous_day else None
        self.execution_tail=session.root/'bars'/session.day
        from .order_tail import decision_quotes
        execution=decision_quotes(self.execution_tail,self)
        execution_seconds=time.perf_counter()-start;scalar_started=time.perf_counter()
        observed=np.zeros((n,u),bool)
        self.raw_extremes={k:np.full((n,u),np.nan,np.float64) for k in ('high','low')}
        self.market={k:execution[k][times,cols] for k in ('mark','bid','ask','quote_us')}
        self.market.update(feature_row=np.arange(p,dtype=np.int64))
        for k in ('volume','notional','trade_count'):self.market[k]=np.zeros(p,np.float64)
        self.indicator_values=np.full((p,len(VARIANTS)),np.nan,np.float32)
        self.enriched_values=np.full((p,len(NAMES)),np.nan,np.float32)
        dollar60=np.zeros(p,np.float64)
        # Bounded per-ticker history scratch; retain points used by inference only.
        for col,ticker in enumerate(self.tickers):
            points=np.flatnonzero(cols==col);targets=times[points]
            frame=trade.filter(pl.col('ticker')==ticker).sort('time_us')
            if not frame.height:raise ValueError('Scanner ticker has no trade bars: '+ticker)
            if not len(points):
                ix=(frame['time_us'].to_numpy()//1000000-clocks[0]).astype(np.int64)
                obs=frame['price_valid'].to_numpy()==1;ext=frame['extremes_valid'].to_numpy()==1
                observed[ix,col]=obs
                for side in self.raw_extremes:self.raw_extremes[side][ix,col]=np.where(obs&ext,frame[side].to_numpy(),np.nan)
                continue
            base,ind,own,dense,obs,ext=scalar_features(frame,clocks,targets,origin)
            # Same elapsed 10-second activity window, only when the previous
            # session actually stored this ticker. No coarse-bar approximation.
            if previous is not None:
                prior=previous.filter(pl.col('ticker')==ticker)
                if prior.height:
                    from research.market_shards.v1.source import price_factor
                    volume=np.zeros(n,np.float64)
                    rows=(prior['time_us'].to_numpy()//1000000-origin_us(previous_day)//1000000-14401).astype(np.int64)
                    if ((rows<0)|(rows>=n)).any():raise ValueError('Previous activity outside premarket')
                    factor=price_factor(session.source['splits'],ticker,previous_day,session.day)
                    volume[rows]=prior['volume'].to_numpy()/factor
                    prior10=rolling_sum(volume,10)[targets]
                    current10=rolling_sum(dense['volume'],10)[targets]
                    available=(targets>=9)&(prior10>0)
                    names={f.name:i for i,f in enumerate(BASE)}
                    base[:,names['log_rvol_10s_prev_session']]=np.where(available,np.log1p(current10/np.maximum(prior10,1e-30)),np.nan)
                    base[:,names['rvol_10s_available']]=available
            self.arrays['features'][points]=np.nan_to_num(base)
            self.arrays['feature_valid'][points]=np.isfinite(base)
            self.indicator_values[points]=ind;self.enriched_values[points,:len(OWN_NAMES)]=own
            observed[:,col]=obs
            for side in self.raw_extremes:self.raw_extremes[side][:,col]=np.where(obs&ext,dense[side],np.nan)
            self.bank['values'][points]=history_values(dense['close'],
                np.where(ext,dense['high'],np.nan),np.where(ext,dense['low'],np.nan),obs,dense['notional'])[targets]
            for k in ('volume','notional','trade_count'):self.market[k][points]=dense[k][targets]
            dollar60[points]=rolling_sum(dense['notional'],60)[targets]
        self._context(times,cols,slots,source,dollar60)
        scalar_seconds=time.perf_counter()-scalar_started;window_started=time.perf_counter()
        tickers=np.array(self.tickers,dtype=object)[cols]
        self.shard_values=materialize(session,tickers,clocks[times]*1000000,self.market['mark'])
        host=dict(execution,observed=observed,membership=membership,history_ids=self.bank['ids'])
        self.host_tensors={k:torch.from_numpy(np.ascontiguousarray(v)) for k,v in host.items()}
        self.tensors=self.host_tensors;self.bytes=sum(v.numel()*v.element_size() for v in self.host_tensors.values())
        if self.bytes>maximum_gib*2**30:raise MemoryError('Execution grid exceeds declared budget')
        self.quote_coverage=dict(version='v65-arte-execution-v1',cells=n*u,
            valid_cells=int(execution['quote_valid'].sum()),listings=u,feature_points=p,
            load_seconds=time.perf_counter()-start,execution_seconds=execution_seconds,
            scalar_seconds=scalar_seconds,window_seconds=time.perf_counter()-window_started)
        paths=[Path(manifest),session.root/'source.json',session.root/'scanner'/session.day/'complete.json']
        for day,name in session.verified_files:paths.append(session.root/'bars'/day/(name+'.parquet'))
        self.file_stats={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in paths}
        session.cache.clear();session.cache_used=0
        if str(device)!='cpu':self.activate(device)

    def _context(self,times,cols,slots,source,dollar60):
        from scipy.stats import rankdata
        safe=slots.clip(0);rows=source[np.arange(len(slots))[:,None],safe]
        ranked=(slots>=0)&(rows>=0);safe_rows=rows.clip(0)
        offset=len(OWN_NAMES)
        for horizon in MARKET_HORIZONS:
            index=OWN_NAMES.index(f'return.{horizon}s')
            values=np.where(ranked,self.enriched_values[safe_rows,index],np.nan).T
            common,percentile=market_summary(values,np.maximum(1,ranked.sum(1)))
            self.enriched_values[:,offset:offset+5]=common[times]
            own=self.enriched_values[:,index]
            self.enriched_values[:,offset+5]=own-common[times,0]
            matrix=np.full(source.shape,np.nan,np.float32)
            rr,kk=np.nonzero(ranked);matrix[rr,slots[rr,kk]]=percentile.T[rr,kk]
            self.enriched_values[:,offset+6]=matrix[times,cols];offset+=7
        values=np.where(ranked,dollar60[safe_rows],np.nan)
        ranks=rankdata(values,axis=1,method='average',nan_policy='omit')
        count=ranked.sum(1);pct=(ranks-1)/np.maximum(1,count[:,None]-1)
        pct[count<2]=np.nan
        matrix=np.full(source.shape,np.nan,np.float32)
        rr,kk=np.nonzero(ranked);matrix[rr,slots[rr,kk]]=pct[rr,kk]
        self.enriched_values[:,offset]=matrix[times,cols]
        previous=np.maximum(0,times-15)
        self.enriched_values[:,offset+1]=np.where(times>=15,matrix[times,cols]-matrix[previous,cols],np.nan)

    def close(self):
        super().close()
        if hasattr(self,'resident_features'):self.resident_features.deactivate()
