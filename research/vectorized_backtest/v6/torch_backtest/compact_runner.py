"""Candidate-specific top-N plus held/order financial slots, shared causal history."""
import torch
from .sparse_runner import SparseProgramRunner
from .holding_registry import HoldingRegistry
from .compact_ledger import compact_ledger_append
from .evolution import STAGES


class CompactProgramRunner(SparseProgramRunner):
    def __init__(self,inputs,space,individuals,gates,*,holding_capacity=40,**kwargs):
        if not kwargs.get('specialize',True):raise ValueError('Compact broker requires parameter specialization')
        if kwargs.get('ledger_mode','inplace')=='atomic':raise ValueError('Compact broker uses an owned in-place ledger')
        top_n=inputs.tensors['top_indices'].shape[1]
        if type(holding_capacity) is not int or holding_capacity<top_n:raise ValueError('Capacity must cover top-N')
        self.candidate_market=True
        super().__init__(inputs,space,individuals,gates,broker_capacity=holding_capacity,**kwargs)
        self.registry=HoldingRegistry(self.b,holding_capacity,top_n,device=inputs.device)
        self.registry_ids=self.registry.ids;self.registry_overflow=self.registry.overflow
        self._state_names.extend(('registry_ids','registry_overflow'))
        # CUDA scatter-reduce has no Bool kernel; store 0/1 identity history.
        self.source_used=torch.zeros((self.b,len(inputs.offsets)-1),dtype=torch.int64,device=inputs.device)
        self.source_swing=torch.full((self.b,len(self.union_ids)),float('nan'),dtype=torch.float64,device=inputs.device)
        self._state_names.extend(('source_used','source_swing'))
        self.source_rings={}
        for name in ('price_ring','close_ring','low_ring','movement_ring','attention_ring'):
            value=torch.full((len(getattr(self,name)),len(self.union_ids)),float('nan'),dtype=torch.float64,device=inputs.device)
            setattr(self,'source_'+name,value);self.source_rings[name]=value;self._state_names.append('source_'+name)
        self.source_previous_close=torch.full((len(self.union_ids),),float('nan'),dtype=torch.float64,device=inputs.device)
        self._state_names.append('source_previous_close')
        self.archived={}
        for name in self._entry_accounting():
            if name=='pending_entry_shares':continue
            value=torch.zeros(self.b,dtype=torch.int64,device=inputs.device)
            setattr(self,'archived_'+name,value);self.archived[name]=value;self._state_names.append('archived_'+name)
        self.ticker_states=[name for name in self._state_names if getattr(self,name).shape[:2]==(self.b,self.n)
            and not name.startswith(('registry_','source_','archived_'))
            and name not in (*self.source_rings,'rule_history','current_atoms')]
        self.tape.provenance.update(version='v6-compact-held-broker-v1',holding_capacity=holding_capacity)
        self.reset()

    def reset(self):
        super().reset()
        if hasattr(self,'registry'):
            self.registry.reset();self.source_used.zero_();self.source_swing.fill_(float('nan'))
            self.source_previous_close.fill_(float('nan'))
            for value in self.source_rings.values():value.fill_(float('nan'))
            for value in self.archived.values():value.zero_()

    def run(self,**kwargs):
        try:
            result=super().run(**kwargs)
        except RuntimeError:
            self.registry.require_valid()
            raise
        self.registry.require_valid()
        return result

    def _entry_accounting(self):
        result=super()._entry_accounting()
        if hasattr(self,'archived'):
            for name,value in self.archived.items():result[name]=result[name]+value
        return result

    def _archive(self,removed):
        values=dict(requested_entry_shares=self.requested_quantity.sum(-1),filled_entry_shares=self.buy_filled.sum(-1),
            filled_entry_orders=(self.buy_filled>0).sum(-1),
            unfilled_entry_orders=((self.requested_quantity>0)&(self.buy_filled==0)).sum(-1),
            partially_filled_entry_orders=((self.buy_filled>0)&(self.buy_filled<self.requested_quantity)).sum(-1),
            entry_retry_count=self.buy_retries.sum(-1),filled_batches=(self.buy_filled.sum(-1)>0).to(torch.int64))
        for name,value in values.items():self.archived[name].add_(torch.where(removed,value,0).sum(-1))

    def tick(self):
        clock=self.tape.clocks.index_select(0,self.index.reshape(1)).squeeze(0)
        top=self.inputs.tensors['top_indices'].index_select(0,self.index.reshape(1)).squeeze(0)
        old=self.registry.ids.clone();axis=self.ticker_axis
        retained=((self.quantity>0)|(self.remaining>0)|(self.reduce_remaining>0)).any(-1)
        retained|=(axis==self.rotation_wait[:,None])&(self.rotation_wait[:,None]>=0)
        retained|=(axis==self.rotation_confirm_ticker[:,None])&(self.rotation_since[:,None]>0)
        self.source_used.scatter_reduce_(1,old.clamp_min(0),(self.used&(old>=0)).to(torch.int64),reduce='amax',include_self=True)
        changed,self.current_membership=self.registry.reconcile(top,retained)
        self.ledger_ticker_order=self.registry.ids.argsort(dim=-1,stable=True)
        self._archive(changed&(old>=0))
        for name in self.ticker_states:
            state=getattr(self,name);mask=changed.reshape(changed.shape+(1,)*(state.ndim-2))
            state.copy_(torch.where(mask,float('nan') if name=='swing_low' else 0,state))
        self.used.copy_(self.source_used.gather(1,self.registry.ids.clamp_min(0)).bool()&(self.registry.ids>=0))
        self.source_market=self.inputs.lookup(self.listing_ids,clock)
        self.source_indices=torch.searchsorted(self.listing_ids,self.registry.ids.clamp_min(0)).clamp_max(len(self.union_ids)-1)
        self.current_market=self.inputs.lookup(self.registry.ids,clock)
        # Call ProgramRunner through SparseProgramRunner's parent: its tick must
        # not replace our candidate-specific market rows with daily-union rows.
        super(SparseProgramRunner,self).tick()
        self.overflow.logical_or_(self.registry.overflow)

    def _row(self,name):
        if name in ('macd_line','macd_signal'):
            return torch.full((self.b,self.n,4),float('nan'),device=self.tape.device,dtype=torch.float64)
        if name in ('structural_clock','structural_targets'):
            if self.structural is None:return torch.zeros_like(self.current_market['observed'])
            rows=self.current_market['source_row'];known=rows>=0
            if name=='structural_clock':return self.structural['valid'][rows.clamp_min(0)]&known&self.current_market['observed']
            return torch.where(known[...,None],self.structural['targets'][rows.clamp_min(0)],float('inf'))
        return super()._row(name)

    def _program_gate(self,stage):
        rows=self.current_market['feature_row'];known=rows>=0
        gate=(self.sparse_gates.gather(1,rows.clamp_min(0)).bitwise_and(1<<STAGES.index(stage))!=0)&known
        if stage=='entry':gate&=self.current_membership
        return gate

    def _select_ticker(self,scores):
        best=scores.amax(-1,keepdim=True)
        ids=torch.where((scores==best)&(self.registry.ids>=0),self.registry.ids,torch.iinfo(torch.int64).max)
        return ids.argmin(-1)

    def _select_lot(self,scores):
        ids=(self.registry.ids[:,:,None]*self.slots+torch.arange(self.slots,device=scores.device)).reshape(self.b,-1)
        return torch.where(scores==scores.amin(-1,keepdim=True),ids,torch.iinfo(torch.int64).max).argmin(-1)

    def _log(self,qty,price,fee,now,side,reason):
        compact_ledger_append(self.ledger_storage,self.fill_count,self.overflow,qty,price,fee,now,reason,
            self.index,self.registry.ids,self.ledger_ticker_order,side,self.maximum_fills)

    def _gather_history(self,value):
        return value[:,self.source_indices].permute(1,0,2)

    def _source_reduce(self,source,name,reduction='mean'):
        values=self._gather_history(source);window=self._value(name,3)
        selected=torch.arange(len(source),device=source.device)[None,:,None]<window
        if reduction=='maximum':return torch.where(selected,values,-float('inf')).amax(1)
        return torch.where(selected,values,0).sum(1)/window.squeeze(1)

    def _recent_high(self):
        return self._source_reduce(self.source_price_ring,'retest_lookback_seconds','maximum')

    def _attention_mean(self):
        return self._source_reduce(self.source_attention_ring,'attention_lookback_seconds')

    def _average_move(self):
        return self._source_reduce(self.source_movement_ring,'adaptive_window')

    def _momentum_close(self):
        values=self._gather_history(self.source_rings['close_ring']).permute(0,2,1)
        indices=(self._value('momentum_lookback_seconds',2).to(torch.int64)-1)[...,None].expand(self.b,self.n,1)
        return values.gather(-1,indices).squeeze(-1)

    def _swing_level(self):return self.source_swing.gather(1,self.source_indices)

    def _advance_movement(self,close,observed):
        market=self.source_market;close=market['mark'];observed=market['observed']
        movement=torch.where(observed&torch.isfinite(self.source_previous_close),
            (close-self.source_previous_close).abs(),float('nan'))
        ring=self.source_rings['movement_ring'];ring.copy_(torch.cat((movement[None],ring[:-1]),0))

    def _advance_source_history(self,close,low,high,observed,notional,above,quote):
        market=self.source_market;observed=market['observed'];close=market['mark']
        for name,field in (('price_ring','high'),('close_ring','mark'),('low_ring','low'),('attention_ring','notional')):
            if name=='close_ring' and self.execution_key[3][0]:continue
            if name=='attention_ring' and self.execution_key[3][1]:continue
            if name=='low_ring' and self.execution_key[1][3]==0:continue
            value=market[field].to(torch.float64)
            if name!='attention_ring':value=torch.where(observed,value,float('nan'))
            ring=self.source_rings[name];ring.copy_(torch.cat((value[None],ring[:-1]),0))
        if self.execution_key[1][3]!=0:
            ring=self.source_rings['low_ring'];right=self._value('swing_right_seconds',2).to(torch.int64)
            length=self._value('swing_left_seconds',3)+self._value('swing_right_seconds',3)+1
            selected=torch.arange(len(ring),device=ring.device)[None,:,None]<length
            values=ring[None].expand(self.b,-1,-1)
            pivot=ring.T[None].expand(self.b,-1,-1).gather(-1,right[...,None].expand(self.b,len(self.union_ids),1)).squeeze(-1)
            confirmed=(~selected|torch.isfinite(values)).all(1)&(pivot==torch.where(selected,values,float('inf')).amin(1))
            self.source_swing.copy_(torch.where(confirmed,pivot,self.source_swing))
        self.source_previous_close.copy_(torch.where(observed,close,float('nan')))
