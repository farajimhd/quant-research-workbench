"""Device lookup and rule compilation over certified sparse observations.

These primitives do not run an optimization or claim broker qualification.
Listing keys remain stable when the decision universe changes.
"""
import json
from pathlib import Path
from time import perf_counter
import numpy as np
import polars as pl
import torch
from .runtime import file_hash
from .compact_prepare import VERSION, KEY_STRIDE
from .feature_bank import CATALOG
from .program import TorchPrograms
from .evolution import STAGES


class SparseInputs:
    def __init__(self, root, *, device='cpu', maximum_gib=8.):
        self.root=Path(root);self.device=torch.device(device)
        self.receipt=json.loads((self.root/'complete.json').read_text())
        if self.receipt.get('identity',{}).get('version')!=VERSION or not self.receipt.get('ready_for_replay') or self.receipt.get('validation_opened'):
            raise ValueError('Require a completed training-only sparse input certificate')
        for name,checksum in self.receipt['files'].items():
            path=(self.root/name).resolve()
            if path.parent!=self.root.resolve() or file_hash(path)!=checksum:raise ValueError('Sparse input file identity/hash changed')
        self.arrays={n:np.load(self.root/(n+'.npy'),mmap_mode='r',allow_pickle=False) for n in
            ('clocks','top_indices','market_keys','feature_keys','features','feature_valid')}
        market=pl.read_parquet(self.root/'market.parquet')
        self.fields={name:market[name].fill_null(False if name=='observed' else -1 if name=='feature_row' else float('nan')).to_numpy() for name in
            ('clock','mark','high','low','observed','volume','notional','fill_price','vwap','bid','ask','quote_us','feature_row','trade_count')}
        self.bytes=sum(x.nbytes for x in self.arrays.values())+sum(x.nbytes for x in self.fields.values())
        if maximum_gib<=0 or self.bytes>maximum_gib*1024**3:raise MemoryError('Sparse input residency exceeds declared envelope')
        self.tensors={n:self._transfer(v) for n,v in self.arrays.items()}
        self.market={n:self._transfer(v) for n,v in self.fields.items()}
        self.offsets=self.receipt['feature_offsets']
        keys=self.arrays['market_keys'];features=self.arrays['feature_keys']
        if not len(keys) or np.any(np.diff(keys)<=0) or np.any(np.diff(features)<=0):raise ValueError('Sparse keys are not strict and unique')
        if len(self.offsets)!=len(self.receipt['listings'])+1 or self.offsets[-1]!=len(features):raise ValueError('Sparse feature identity offsets changed')

    def _transfer(self, array):
        host=torch.from_numpy(np.array(array,copy=True))
        if self.device.type=='cuda':host=host.pin_memory()
        return host.to(self.device,non_blocking=True)

    def lookup(self, listing, clock, *, maximum_quote_age_seconds=1.):
        """Arbitrary candidate/holding axes; carries never invent execution bars."""
        keys=self.tensors['market_keys'];query=listing.to(torch.int64)*KEY_STRIDE+clock
        row=torch.searchsorted(keys,query,right=True)-1;safe=row.clamp(0,len(keys)-1)
        known=(row>=0)&(listing>=0)&(keys[safe]//KEY_STRIDE==listing)&(keys[safe]%KEY_STRIDE<=clock)
        current=known&(keys[safe]%KEY_STRIDE==clock)
        result={name:value[safe] for name,value in self.market.items()}
        result['known']=known;result['current']=current;result['source_row']=torch.where(known,row,-1)
        result['observed']=result['observed']&current
        for name in ('volume','notional','trade_count'):
            result[name]=torch.where(current,result[name],0.)
        for name in ('high','low','fill_price'):
            result[name]=torch.where(current,result[name],float('nan'))
        for name in ('mark','vwap','bid','ask'):
            result[name]=torch.where(known,result[name],float('nan'))
        quote=result['quote_us'];age=clock*1000000-quote
        result['quote_valid']=known&(quote>0)&(age>=0)&(age<=maximum_quote_age_seconds*1000000)&torch.isfinite(result['bid'])&(result['bid']>0)&(result['ask']>=result['bid'])
        result['feature_row']=torch.where(known,result['feature_row'],-1).to(torch.int64)
        return result

    def compile(self, individuals, *, chunk_candles=2048, listing_batch=16, workspace_gib=2., maximum_gate_gib=4.):
        """[candidate, observed row] gates, never [clock,candidate,all tickers]."""
        began=perf_counter();b=len(individuals);r=len(self.arrays['feature_keys'])
        if not b or min(chunk_candles,listing_batch)<1 or workspace_gib<=0 or b*r>maximum_gate_gib*1024**3:
            raise MemoryError('Invalid or excessive sparse rule workspace/gates')
        gates=torch.zeros((b,r),dtype=torch.uint8,device=self.device)
        programs={stage:TorchPrograms([v.programs()[stage] for v in individuals],CATALOG,self.device) for stage in STAGES}
        buckets={};width=max(p.width for p in programs.values())
        for left,right in zip(self.offsets[:-1],self.offsets[1:]):
            for begin in range(left,right,chunk_candles):
                warm=max(left,begin-119);end=min(right,begin+chunk_candles)
                size=1<<(end-warm-1).bit_length();buckets.setdefault(size,[]).append((warm,begin,end))
        for size,tasks in buckets.items():
            per_listing=size*(len(CATALOG)*5+b*(width*20+128)+32)
            batch_size=min(listing_batch,int(workspace_gib*1024**3)//per_listing)
            if batch_size<1:raise MemoryError('Sparse rule workspace cannot fit one listing chunk')
            for cursor in range(0,len(tasks),batch_size):
                task=tasks[cursor:cursor+batch_size];n=len(task)
                values=torch.zeros((n,size,len(CATALOG)),device=self.device)
                valid=torch.zeros_like(values,dtype=torch.bool)
                indices=torch.zeros((n,size),device=self.device,dtype=torch.int64)
                core=torch.zeros((n,size),device=self.device,dtype=torch.bool)
                for i,(warm,begin,end) in enumerate(task):
                    length=end-warm;values[i,:length]=self.tensors['features'][warm:end];valid[i,:length]=self.tensors['feature_valid'][warm:end]
                    indices[i,:length]=torch.arange(warm,end,device=self.device);core[i,begin-warm:length]=True
                for bit,stage in enumerate(STAGES):
                    value,known=programs[stage](values,valid)
                    signal=((value!=0)&known&core[None]).to(torch.uint8)*(1<<bit)
                    gates.scatter_add_(1,indices.reshape(1,-1).expand(b,-1),signal.reshape(b,-1))
        if self.device.type=='cuda':torch.cuda.current_stream().synchronize()
        return gates,perf_counter()-began
