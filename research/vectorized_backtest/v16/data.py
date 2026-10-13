"""Thin device prices and ranks; stream full causal 1-second features from CPU."""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import polars as pl
import torch
from .sparse_replay import verify_sparse_receipt
from .persisted_history import load_history
from research.vectorized_backtest.v6.torch_backtest.compact_prepare import KEY_STRIDE
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from .features import CATALOG,BASE,PRICE_COLUMNS,LIQUIDITY


class SessionData:
    def __init__(self,root,history,*,device='cpu',maximum_gib=4.,maximum_history_gib=16.,feature_cache=None,indicator_root=None):
        self.root=Path(root);self.device=torch.device(device)
        self.receipt=verify_sparse_receipt(self.root)
        self.arrays={n:np.load(self.root/(n+'.npy'),mmap_mode='r',allow_pickle=False) for n in
                ('clocks','top_indices','market_keys','feature_keys','features','feature_valid')}
        self.bank,cert=load_history(SimpleNamespace(root=self.root,arrays=self.arrays,receipt=self.receipt),history,maximum_history_gib)
        clocks=self.arrays['clocks'];union=np.asarray(cert['listing_ids']);self.listing_ids=union
        market=pl.read_parquet(self.root/'market.parquet',columns=['mark','observed','feature_row','bid','ask','quote_us','volume','notional','trade_count'])
        self.market={n:market[n].fill_null(False if n=='observed' else -1 if n=='feature_row' else float('nan')).to_numpy() for n in market.columns}
        source=self.bank['source_rows'];known=source>=0;safe=source.clip(0)
        keys=self.arrays['market_keys'][safe]
        mark=self.market['mark'][safe].astype(np.float64);mark[~known]=np.nan
        observed=self.market['observed'][safe]&known&(keys%KEY_STRIDE==clocks[:,None])
        membership=np.zeros(source.shape,dtype=bool)
        for slot in range(self.arrays['top_indices'].shape[1]):
            top=self.arrays['top_indices'][:,slot];columns=np.searchsorted(union,top).clip(0,len(union)-1)
            membership[np.arange(len(clocks))[top>=0],columns[top>=0]]=True
        host=dict(mark=mark,observed=observed,membership=membership,history_ids=np.asarray(self.bank['ids']))
        self.bytes=sum(v.nbytes for v in host.values())
        if maximum_gib<=0 or self.bytes>maximum_gib*1024**3:raise MemoryError('V7 price/rank residency exceeds declared budget')
        self.host_tensors={k:torch.from_numpy(np.array(v,copy=True)) for k,v in host.items()}
        self.tensors={k:v.to(self.device) for k,v in self.host_tensors.items()}
        self.clocks=len(clocks)
        self.identity=dict(input_sha256=file_hash(self.root/'complete.json'),history_sha256=file_hash(Path(history)/'complete.json'))
        self.file_stats={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for folder in (self.root,Path(history)) for p in folder.iterdir() if p.is_file()}
        self.feature_cache=None
        self.indicator_values=None
        if indicator_root is not None:
            import json
            from .features import VERSION,VARIANTS
            folder=Path(indicator_root);certificate=json.loads((folder/'complete.json').read_text(encoding='utf-8-sig'))
            path=folder/'values.npy'
            if (certificate.get('version')!=VERSION or certificate.get('status')!='complete'
                or certificate['input_sha256']!=self.identity['input_sha256']
                or certificate['definition']['math_sha256']!=file_hash(Path(__file__).with_name('indicator_math.py'))
                or bool(certificate['validation_opened'])!=bool(self.receipt['validation_opened'])
                or file_hash(path)!=certificate['values_sha256']):raise ValueError('Continuous indicator bank identity/integrity changed')
            self.indicator_values=np.load(path,mmap_mode='r',allow_pickle=False)
            if self.indicator_values.dtype!=np.float32 or self.indicator_values.shape!=(len(self.arrays['features']),len(VARIANTS)):raise ValueError('Indicator shape/type mismatch')
            self.identity['indicator_sha256']=file_hash(folder/'complete.json')
            for p in (folder/'complete.json',path):self.file_stats[str(p)]=(p.stat().st_size,p.stat().st_mtime_ns)
        if feature_cache is not None:
            from research.vectorized_backtest.v7.feature_cache import FeatureCache
            self.feature_cache=FeatureCache(feature_cache,self)

    def activate(self,device):
        for name,expected in self.file_stats.items():
            p=Path(name)
            if (p.stat().st_size,p.stat().st_mtime_ns)!=expected:raise ValueError('Cached certified input changed')
        self.device=torch.device(device);self.tensors={k:v.to(self.device) for k,v in self.host_tensors.items()}

    def deactivate(self):
        self.device=torch.device('cpu');self.tensors=self.host_tensors

    def feature_block(self,begin,end,listings):
        if self.feature_cache is not None:
            values,valid=self.feature_cache.block(begin,end,listings)
            values,valid=self.append_indicators(values,valid,begin,end,listings)
            return torch.from_numpy(values).to(self.device),torch.from_numpy(valid).to(self.device)
        return self.prepare_feature_block(begin,end,listings)

    def prepare_feature_block(self,begin,end,listings):
        """Ticker x elapsed-second x feature, quote snapshots as-of second close."""
        rows=self.bank['source_rows'][begin:end,listings].T
        known=rows>=0;safe=rows.clip(0)
        feature_rows=self.market['feature_row'][safe].astype(np.int64)
        feature_known=known&(feature_rows>=0);feature_safe=feature_rows.clip(0)
        base=self.arrays['features'][feature_safe];base_valid=self.arrays['feature_valid'][feature_safe]&feature_known[...,None]
        ids=self.bank['ids'][begin:end,listings].T
        history=self.bank['values'][ids].astype(np.float32)
        price=self.market['mark'][safe]
        relative=(history[...,PRICE_COLUMNS]/price[...,None]-1).astype(np.float32)
        clock=self.arrays['clocks'][begin:end][None]
        bid=self.market['bid'][safe];ask=self.market['ask'][safe];quote=self.market['quote_us'][safe]
        age=clock-quote/1e6
        quote_valid=known&(quote>0)&(age>=0)&(age<=1)&(bid>0)&(ask>=bid)
        current=known&(self.arrays['market_keys'][safe]%KEY_STRIDE==clock)
        liquidity=np.stack((bid/price-1,ask/price-1,(ask-bid)/price,age,
            *(np.log1p(np.where(current,self.market[n][safe],0.)) for n in ('volume','notional','trade_count')),
            bid,ask,ask-bid,price),-1)
        values=np.concatenate((base,history,relative,liquidity),-1).astype(np.float32)
        valid=np.concatenate((base_valid,np.isfinite(history)&known[...,None],
            np.isfinite(relative)&known[...,None]&(price>0)[...,None],np.isfinite(liquidity)&known[...,None]),-1)
        valid[...,-len(LIQUIDITY):-len(LIQUIDITY)+3]&=quote_valid[...,None]
        valid[...,-len(LIQUIDITY)+3]&=(quote>0)&(age>=0)
        valid[...,-4:-1]&=quote_valid[...,None]
        valid[...,-1]&=price>0
        values[~valid]=0.
        values,valid=self.append_indicators(values,valid,begin,end,listings)
        return torch.from_numpy(values).to(self.device),torch.from_numpy(valid).to(self.device)

    def append_indicators(self,values,valid,begin,end,listings):
        from .features import VARIANTS
        if self.indicator_values is None:
            shape=(*values.shape[:-1],len(VARIANTS));v=np.zeros(shape,np.float32);m=np.zeros(shape,bool)
        else:
            rows=self.bank['source_rows'][begin:end,listings].T;feature=self.market['feature_row'][rows.clip(0)].astype(np.int64)
            v=self.indicator_values[feature.clip(0)];m=np.isfinite(v)&(rows>=0)[...,None]&(feature>=0)[...,None];v=np.where(m,v,0.)
        return np.concatenate((values,v),-1),np.concatenate((valid,m),-1)

    def swing_bank(self,members,maximum_gib=2.):
        from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
        columns=[211+SWING_WINDOWS.index(m.policy.swing_left)*11+SWING_WINDOWS.index(m.policy.swing_right)
                 if m.policy.swing_left else 211 for m in members]
        unique=np.unique(columns);required=len(self.bank['values'])*len(unique)*8
        if required>maximum_gib*1024**3:raise MemoryError('V7 projected swing bank exceeds explicit envelope')
        values=np.array(self.bank['values'][:,unique],copy=True)
        return torch.from_numpy(values).to(self.device),torch.tensor(np.searchsorted(unique,columns),device=self.device)

    def close(self):
        self.tensors={};self.host_tensors={}
        if self.feature_cache is not None:self.feature_cache.close()
