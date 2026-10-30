"""Bounded execution-only pages; never expose post-cutoff data to the model."""
import json
from pathlib import Path
import numpy as np
import polars as pl
from .prepare_execution import sha,VERSION


class Tail:
    def __init__(self,root,item):
        self.root=Path(root);self.item=item
        self.receipt=json.loads((self.root/'complete.json').read_bytes())
        identity=self.receipt['identity']
        if identity['version']!=VERSION or identity['input_receipt_sha256']!=item.identity['input_sha256'] or identity['history_receipt_sha256']!=item.identity['history_sha256'] or identity['listing_ids']!=item.listing_ids.tolist():
            raise ValueError('Execution tail does not match this session/listing order')
        if identity['cutoff_us']!=int(item.arrays['clocks'][-1])*1000000:raise ValueError('Execution cutoff changed')
        self.tickers=identity['tickers'];self.mapping={t:i for i,t in enumerate(self.tickers)}
        source=np.asarray(item.bank['source_rows'][-1]);src=source.clip(0)
        self.last=np.column_stack([np.asarray(item.market[k])[src] for k in ('mark','bid','ask','quote_us')]).astype(np.float64)
        self.last[source<0]=np.nan
    def load(self,index):
        page=self.receipt['pages'][index];path=self.root/page['file']
        if sha(path)!=page['sha256']:raise ValueError('Certified execution page changed')
        frame=pl.read_parquet(path).with_columns(pl.col('ticker').replace_strict(self.mapping).alias('column'))
        start=page['begin_us']//1000000;end=page['end_us']//1000000
        clocks=np.arange(start+1,end+1,dtype=np.int64);u=len(self.tickers)
        updates=np.full((len(clocks),u,4),np.nan,dtype=np.float64)
        capacity=np.zeros((len(clocks),u),dtype=np.float64)
        row=(frame['time_us'].to_numpy()//1000000-start-1).astype(np.int64);col=frame['column'].to_numpy().astype(np.int64)
        capacity[row,col]=frame['capacity'].to_numpy()
        for channel,name in enumerate(('mark','bid','ask','quote_us')):
            v=frame[name].to_numpy().astype(np.float64)
            usable=np.isfinite(v)&(v>0)
            if name in ('bid','ask'):usable&=frame['quote_us'].to_numpy()>0
            updates[row[usable],col[usable],channel]=v[usable]
        # Vectorized forward fill per listing/field, seeded by the preceding page.
        bank=np.concatenate((self.last[None],updates),axis=0)
        known=np.isfinite(bank);idx=np.maximum.accumulate(np.where(known,np.arange(len(bank))[:,None,None],0),axis=0)
        bank=np.take_along_axis(bank,idx,axis=0)[1:];self.last=bank[-1].copy()
        mark,bid,ask,stamp=np.moveaxis(bank,-1,0)
        age=clocks[:,None]-stamp/1e6
        valid=np.isfinite(bid)&np.isfinite(ask)&(bid>0)&(ask>=bid)&(stamp>0)&(age>=0)&(age<=1.)
        return dict(mark=np.ascontiguousarray(mark),bid=np.ascontiguousarray(bid),ask=np.ascontiguousarray(ask),
                    quote_valid=valid,capacity=capacity,clocks=clocks)
