"""Bounded execution-only pages; never expose post-cutoff data to the model."""
import json
from pathlib import Path
import numpy as np
import polars as pl
from .prepare_execution import sha,VERSION


CHANNELS=('mark','bid','ask','quote_us','bid_size','ask_size','book_valid')


class Tail:
    def __init__(self,root,item,decision=False):
        self.root=Path(root);self.item=item
        self.receipt=json.loads((self.root/'complete.json').read_bytes())
        identity=self.receipt['identity']
        if identity['version']!=VERSION or identity['input_receipt_sha256']!=item.identity['input_sha256'] or identity['history_receipt_sha256']!=item.identity['history_sha256'] or identity['listing_ids']!=item.listing_ids.tolist():
            raise ValueError('Execution tail does not match this session/listing order')
        if identity['cutoff_us']!=int(item.arrays['clocks'][-1])*1000000:raise ValueError('Execution cutoff changed')
        self.receipt=dict(self.receipt,pages=[p for p in self.receipt['pages'] if p['phase']==('decision' if decision else 'tail')])
        self.tickers=identity['tickers'];self.mapping={t:i for i,t in enumerate(self.tickers)}
        self.last=np.zeros((len(self.tickers),len(CHANNELS)),dtype=np.float64)
        if not decision:self.last=np.array(item.execution_quote_seed,copy=True)
    def load(self,index):
        page=self.receipt['pages'][index];path=self.root/page['file']
        if sha(path)!=page['sha256']:raise ValueError('Certified execution page changed')
        frame=pl.read_parquet(path,columns=['ticker','time_us',*CHANNELS]).with_columns(pl.col('ticker').replace_strict(self.mapping).alias('column'))
        start=page['begin_us']//1000000;end=page['end_us']//1000000
        clocks=np.arange(start+1,end+1,dtype=np.int64);u=len(self.tickers)
        updates=np.full((len(clocks),u,len(CHANNELS)),np.nan,dtype=np.float64)
        row=(frame['time_us'].to_numpy()//1000000-start-1).astype(np.int64);col=frame['column'].to_numpy().astype(np.int64)
        v=frame['mark'].to_numpy().astype(np.float64);usable=np.isfinite(v)&(v>0)
        updates[row[usable],col[usable],0]=v[usable]
        quoted=frame['quote_us'].to_numpy()>0
        book=frame.select(CHANNELS[1:]).to_numpy().astype(np.float64)
        if not np.isfinite(book).all() or (book[:,3:5]<0).any():raise ValueError('Invalid certified displayed quote size')
        # A withdrawn/invalid new book replaces the previous snapshot too.
        updates[row[quoted],col[quoted],1:]=book[quoted]
        # Vectorized forward fill per listing/field, seeded by the preceding page.
        bank=np.concatenate((self.last[None],updates),axis=0)
        known=np.isfinite(bank);idx=np.maximum.accumulate(np.where(known,np.arange(len(bank))[:,None,None],0),axis=0)
        bank=np.take_along_axis(bank,idx,axis=0)[1:];self.last=bank[-1].copy()
        mark,bid,ask,stamp,bid_size,ask_size,book_valid=np.moveaxis(bank,-1,0)
        age=clocks[:,None]-stamp/1e6
        valid=(book_valid>0)&np.isfinite(bid)&np.isfinite(ask)&(bid>0)&(ask>=bid)&(stamp>0)&(age>=0)
        return dict(mark=np.ascontiguousarray(mark),bid=np.ascontiguousarray(bid),ask=np.ascontiguousarray(ask),
                    quote_valid=valid,clocks=clocks,quote_us=stamp,bid_size=bid_size,ask_size=ask_size)


def decision_quotes(root,item):
    reader=Tail(root,item,decision=True);clocks=np.asarray(item.arrays['clocks']);u=len(item.listing_ids)
    keys=('bid','ask','quote_us','bid_size','ask_size','quote_valid')
    result={k:np.zeros((len(clocks),u),dtype=bool if k=='quote_valid' else np.float64) for k in keys}
    for index in range(len(reader.receipt['pages'])):
        page=reader.load(index);rows=page['clocks']-clocks[0]
        if (rows<0).any() or (rows>=len(clocks)).any():raise ValueError('Execution quote page crossed decision window')
        for key in keys:result[key][rows]=page[key]
    item.execution_quote_seed=reader.last.copy()
    return result
