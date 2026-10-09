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


def verify_sparse_receipt(root):
    """Verify the immutable boundary without allocating feature/device arrays."""
    root=Path(root)
    receipt=json.loads((root/'complete.json').read_text())
    if receipt.get('identity',{}).get('version')!=VERSION or not receipt.get('ready_for_replay') or receipt.get('validation_opened'):
        raise ValueError('Require a completed training-only sparse input certificate')
    for name,checksum in receipt['files'].items():
        path=(root/name).resolve()
        if path.parent!=root.resolve() or file_hash(path)!=checksum:
            raise ValueError('Sparse input file identity/hash changed')
    return receipt


class SparseInputs:
    def __init__(self, root, *, device='cpu', maximum_gib=8.):
        self.root=Path(root);self.device=torch.device(device)
        if self.device.type=='cuda' and self.device.index is None:
            self.device=torch.device('cuda',torch.cuda.current_device())
        self.receipt=verify_sparse_receipt(self.root)
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

    def lookup(self, listing, clock, *, maximum_quote_age_seconds=1., source_rows=None):
        """Arbitrary candidate/holding axes; carries never invent execution bars."""
        keys=self.tensors['market_keys'];query=listing.to(torch.int64)*KEY_STRIDE+clock
        row=torch.searchsorted(keys,query,right=True)-1 if source_rows is None else torch.where(listing>=0,source_rows,-1)
        safe=row.clamp(0,len(keys)-1)
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

    def compile(self, individuals, *, chunk_candles=2048, listing_batch=16, workspace_gib=2., maximum_gate_gib=4., listing_ids=None,backend='eager',prepared=None):
        """[candidate, observed row] gates, never [clock,candidate,all tickers]."""
        began=perf_counter();b=len(individuals);r=len(self.arrays['feature_keys'])
        if backend not in ('eager','cudagraph') or (backend=='cudagraph' and self.device.type!='cuda'):
            raise ValueError('Rule capture requires CUDA and a caller-owned preparation barrier')
        if not b or min(chunk_candles,listing_batch)<1 or workspace_gib<=0 or b*r>maximum_gate_gib*1024**3:
            raise MemoryError('Invalid or excessive sparse rule workspace/gates')
        gates=torch.zeros((b,r),dtype=torch.uint8,device=self.device)
        if prepared is not None and (tuple(individuals)!=prepared.members or self.device!=prepared.device or backend!='cudagraph'):
            raise ValueError('Shared rule batch identity/device/backend changed')
        programs=prepared.programs if prepared is not None else {stage:TorchPrograms([v.programs()[stage] for v in individuals],CATALOG,self.device) for stage in STAGES}
        buckets={};width=max(p.width for p in programs.values())
        selected=set(range(len(self.offsets)-1)) if listing_ids is None else set(listing_ids)
        if any(type(i) is not int or not 0<=i<len(self.offsets)-1 for i in selected):raise ValueError('Invalid sparse rule listing identity')
        for listing,(left,right) in enumerate(zip(self.offsets[:-1],self.offsets[1:])):
            if listing not in selected:continue
            for begin in range(left,right,chunk_candles):
                warm=max(left,begin-119);end=min(right,begin+chunk_candles)
                size=1<<int(end-warm-1).bit_length();buckets.setdefault(size,[]).append((warm,begin,end))
        for size,tasks in buckets.items():
            captures={}
            per_listing=size*(len(CATALOG)*5+b*(width*20+128)+32)
            batch_size=min(listing_batch,int(workspace_gib*1024**3)//per_listing)
            if batch_size<1:raise MemoryError('Sparse rule workspace cannot fit one listing chunk')
            for cursor in range(0,len(tasks),batch_size):
                task=tasks[cursor:cursor+batch_size];n=batch_size if prepared is not None else len(task)
                if backend=='cudagraph':
                    from .captured_rules import CapturedRules
                    if n not in captures:captures[n]=prepared.capture((n,size,len(CATALOG))) if prepared is not None else CapturedRules(programs,(n,size,len(CATALOG)),self.device)
                    values=captures[n].values;valid=captures[n].valid
                else:
                    values=torch.empty((n,size,len(CATALOG)),device=self.device)
                    valid=torch.empty_like(values,dtype=torch.bool)
                # Gather the complete listing batch in one operation instead of
                # launching copies/aranges separately for every history chunk.
                bounds=torch.tensor(task+[(0,0,0)]*(n-len(task)),device=self.device,dtype=torch.int64)
                position=torch.arange(size,device=self.device)[None]
                present=position<(bounds[:,2]-bounds[:,0])[:,None]
                indices=torch.where(present,bounds[:,0,None]+position,0)
                core=present&(position>=(bounds[:,1]-bounds[:,0])[:,None])
                torch.index_select(self.tensors['features'],0,indices.reshape(-1),out=values.view(-1,len(CATALOG)))
                values.masked_fill_(~present[...,None],0)
                torch.index_select(self.tensors['feature_valid'],0,indices.reshape(-1),out=valid.view(-1,len(CATALOG)))
                valid.logical_and_(present[...,None])
                if backend=='cudagraph':
                    signal=torch.where(core[None],captures[n].replay(),0)
                    gates.scatter_add_(1,indices.reshape(1,-1).expand(b,-1),signal.reshape(b,-1))
                else:
                    for bit,stage in enumerate(STAGES):
                        value,known=programs[stage](values,valid)
                        signal=((value!=0)&known&core[None]).to(torch.uint8)*(1<<bit)
                        gates.scatter_add_(1,indices.reshape(1,-1).expand(b,-1),signal.reshape(b,-1))
            if backend=='cudagraph':
                # Release each shape bucket only after its last replay/scatter.
                # Do not retain a graph pool for every possible chunk shape.
                torch.cuda.current_stream().synchronize();captures.clear()
        if self.device.type=='cuda':torch.cuda.current_stream().synchronize()
        return gates,perf_counter()-began
