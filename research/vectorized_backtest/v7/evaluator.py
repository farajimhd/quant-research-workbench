"""Vectorized position lanes; chronology is required only inside each lane."""
from dataclasses import dataclass
import math
import torch
from research.vectorized_backtest.v6.torch_backtest.program import TorchPrograms
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
from .features import CATALOG,BASE
from .genome import STAGES

@dataclass(frozen=True)
class Execution:
    entry_dollars:float=1000.
    add_dollars:float=200.
    cost_bps:float=0.
    def validate(self):
        if any(not math.isfinite(v) or v<0 for v in (self.entry_dollars,self.add_dollars,self.cost_bps)) or min(self.entry_dollars,self.add_dollars)<=0:
            raise ValueError('Invalid assumed-fill sizing/cost')
        return self


def step(position,aggregate,price,previous,observed,membership,signals,swing,policy,clock:torch.Tensor,terminal,entry_dollars:float,add_dollars:float,cost:float):
    """[session,strategy,ticker] lanes; all signals predate the execution price.

    Position fields: quantity, average cost, stop, next target, high water,
    adds, last management clock, realized P&L, episode start P&L.
    Aggregate: peak equity, drawdown, capital seconds, risk seconds,
    entries, adds, reductions, inactive seconds, worst complete episode, episodes.
    """
    q,avg,stop,target,water,adds,last,realized,episode=position.unbind(-1)
    held=q>1e-12;tradable=observed&torch.isfinite(price)&(price>0)
    profit=previous/avg.clamp_min(1e-12)-1
    trail=(signals&16)!=0
    ratchet=held&trail&torch.isfinite(previous)
    water=torch.where(ratchet,torch.maximum(water,previous),water)
    stop=torch.where(ratchet,torch.maximum(stop,water*(1-policy[...,3])),stop)
    exit_rule=(signals&2)!=0
    terminal_price=torch.isfinite(price)&(price>0)
    exit_now=held&((terminal&terminal_price)|((exit_rule|(previous<=stop))&tradable))
    managed=(clock-last)>=policy[...,7]
    target_hit=held&(previous>=target)
    reduce_now=held&~exit_now&tradable&managed&(((signals&8)!=0)&(profit>=policy[...,5])|target_hit)
    sell=torch.where(exit_now,q,torch.where(reduce_now,q*policy[...,2],torch.zeros_like(q)))
    execution_price=torch.where(torch.isfinite(price),price,torch.zeros_like(price))
    realized=realized+sell*(execution_price-avg)-sell*execution_price*cost
    remaining=(q-sell).clamp_min(0)
    closed=held&(remaining<=1e-12)
    complete_profit=realized-episode
    episode_worst=torch.where(closed,complete_profit,torch.full_like(q,float('inf'))).amin(-1)
    add_now=held&~exit_now&~reduce_now&tradable&managed&((signals&4)!=0)&(profit>=policy[...,4])&(adds<policy[...,6])
    entry_now=~held&~terminal&tradable&membership&((signals&1)!=0)
    initial=execution_price*(1-policy[...,0])
    swing_mode=policy[...,8]>0
    initial=torch.where(swing_mode,swing,initial)
    entry_now&=torch.isfinite(initial)&(initial>0)&(initial<execution_price)
    # Scalar-only where() defaults to float32, including dollar fee arithmetic.
    dollars=entry_now.to(q.dtype)*entry_dollars+add_now.to(q.dtype)*add_dollars
    bought=dollars/execution_price.clamp_min(1e-12)
    quantity=remaining+bought
    # Sales and elapsed clocks never change the surviving shares' cost basis.
    # Repeated multiply/divide was drifting at exact zero-profit gates under
    # compilation. Entry basis is its actual fill; only additions reweight it.
    added_avg=avg+(bought/quantity.clamp_min(1e-12))*(execution_price-avg)
    new_avg=torch.where(entry_now,execution_price,torch.where(add_now,added_avg,
        torch.where(quantity>0,avg,torch.zeros_like(avg))))
    episode=torch.where(entry_now,realized,episode)
    realized=realized-dollars*cost
    stop=torch.where(entry_now,initial,stop)
    target=torch.where(entry_now,execution_price*(1+policy[...,1]),torch.where(target_hit&reduce_now,target+avg*policy[...,1],target))
    water=torch.where(entry_now,execution_price,water)
    adds=torch.where(entry_now,torch.zeros_like(adds),adds+add_now.to(adds.dtype))
    last=torch.where(entry_now|add_now|reduce_now,clock,last)
    position=torch.stack((quantity,new_avg,stop,target,water,adds,last,realized,episode),-1)
    equity=(realized+quantity*(execution_price-new_avg)).sum(-1)
    peak=torch.maximum(aggregate[...,0],equity)
    dd=torch.maximum(aggregate[...,1],peak-equity)
    capital=(quantity*execution_price).sum(-1)
    risk=(quantity*(execution_price-stop).clamp_min(0)).sum(-1)
    aggregate=torch.stack((peak,dd,aggregate[...,2]+capital,aggregate[...,3]+risk,
        aggregate[...,4]+entry_now.sum(-1),aggregate[...,5]+add_now.sum(-1),aggregate[...,6]+reduce_now.sum(-1),
        aggregate[...,7]+(~(quantity>0).any(-1)).to(equity.dtype),torch.minimum(aggregate[...,8],episode_worst),
        aggregate[...,9]+closed.sum(-1)),-1)
    return position,aggregate


class PopulationPrograms:
    """Pack once per candidate batch; reuse the same tensors across sessions."""
    def __init__(self,members,device='cpu'):
        for member in members:member.validate()
        self.members=members;self.device=torch.device(device)
        self.open_programs={s:TorchPrograms([m.open_rules[s] if m.open_rules is not None else m.rules[s] for m in members],CATALOG,self.device) for s in STAGES}
        self.programs={s:TorchPrograms([m.rules[s] for m in members],CATALOG,self.device) for s in STAGES}
    def evaluate(self,data,chunk=2048,listing_batch=4,workspace_gib=2.,maximum_gate_gib=2.):
        b=len(self.members);u=len(data.listing_ids)
        if 2*b*data.clocks*u>maximum_gate_gib*1024**3:raise MemoryError('V7 rule gates exceed declared budget')
        gates=torch.zeros((b,data.clocks,u),dtype=torch.int16,device=self.device)
        width=max(v.width for v in (*self.programs.values(),*self.open_programs.values()))
        estimate=(chunk+119)*(len(CATALOG)*5+b*(width*20+64))*listing_batch
        if estimate>workspace_gib*1024**3:raise MemoryError('V7 rule workspace exceeds declared budget')
        for first in range(0,u,listing_batch):
            listings=list(range(first,min(u,first+listing_batch)))
            for begin in range(0,data.clocks,chunk):
                warm=max(0,begin-119);end=min(data.clocks,begin+chunk)
                inputs,known=data.feature_block(warm,end,listings)
                for branch,programs in enumerate((self.programs,self.open_programs)):
                    for bit,stage in enumerate(STAGES):
                        signal,mask=programs[stage](inputs,known)
                        gates[:,begin:end,first:first+len(listings)]|=((signal[:,:,begin-warm:]!=0)&mask[:,:,begin-warm:]).transpose(1,2).to(torch.int16)*(1<<(bit+8*branch))
        return gates


def state_signals(signals,held,age,limits,stateful:bool):
    if stateful:signals=torch.where(held,signals>>8,signals)&255
    allowed=(age.clamp_min(0)[...,None]>=limits)|~held[...,None]
    mask=allowed[...,0].to(torch.int32)+2*allowed[...,1].to(torch.int32)+4*allowed[...,2].to(torch.int32)+8*allowed[...,3].to(torch.int32)+16*allowed[...,4].to(torch.int32)
    return signals&mask


def replay_cohort(data,members,gates,*,execution=Execution(),backend='eager',progress=None):
    """One batched position engine across independent sessions and strategies.

    No bid/ask, spread, volume-capacity, cash, or pending-order tensors enter
    this function. GPU compilation specializes the small transition kernel.
    """
    execution.validate()
    if not data or len(data)!=len(gates) or backend not in ('eager','compile'):raise ValueError('Invalid replay cohort')
    device=data[0].device;b=len(members);s=len(data);u=max(len(v.listing_ids) for v in data);t=max(v.clocks for v in data)
    if any(v.device!=device for v in data):raise ValueError('Cohort devices differ')
    position=torch.zeros((s,b,u,9),dtype=torch.float64,device=device)
    aggregate=torch.zeros((s,b,10),dtype=torch.float64,device=device);aggregate[...,8]=float('inf')
    policy=torch.tensor([[m.policy.stop_fraction,m.policy.target_fraction,m.policy.reduce_fraction,m.policy.trail_fraction,
        m.policy.add_minimum_profit,m.policy.reduce_minimum_profit,m.policy.maximum_adds,m.policy.cooldown,m.policy.swing_left] for m in members],dtype=torch.float64,device=device)[None,:,None,:]
    swing_banks=[v.swing_bank(members) for v in data]
    # Pack the cohort once. The chronological loop has no per-session host
    # dispatch, tensor construction, or policy repacking.
    from torch.nn.functional import pad
    required=t*s*b*u*gates[0].element_size()+sum(v.numel()*v.element_size() for v,c in swing_banks)
    required+=t*s*u*40+s*b*u*9*8
    if device.type=='cuda':
        free,_=torch.cuda.mem_get_info(device)
        if required>free*.8:raise MemoryError('Packed V7 cohort exceeds GPU headroom')
    prices=torch.stack([pad(v.tensors['mark'],(0,u-len(v.listing_ids),0,t-v.clocks),value=float('nan')) for v in data],1)[:,:,None]
    observed_all=torch.stack([pad(v.tensors['observed'],(0,u-len(v.listing_ids),0,t-v.clocks),value=False) for v in data],1)[:,:,None]
    membership_all=torch.stack([pad(v.tensors['membership'],(0,u-len(v.listing_ids),0,t-v.clocks),value=False) for v in data],1)[:,:,None]
    all_signals=torch.stack([pad(g,(0,u-len(v.listing_ids),0,t-v.clocks)) for v,g in zip(data,gates)],1).permute(2,1,0,3)
    values=torch.cat([bank for bank,columns in swing_banks],0);columns=swing_banks[0][1]
    ids=[];offset=0
    for item,(bank,_) in zip(data,swing_banks):
        ids.append(pad(item.tensors['history_ids'].to(torch.int64)+offset,(0,u-len(item.listing_ids),0,t-item.clocks),value=offset))
        offset+=len(bank)
    all_ids=torch.stack(ids,1);del swing_banks
    opened=torch.zeros((s,b,u),device=device,dtype=torch.float64)
    age_limits=torch.tensor([[m.minimum_age.get(stage,0) for stage in STAGES] for m in members],device=device,dtype=torch.float64)[None,:,None,:]
    stateful=all(g.dtype==torch.int16 for g in gates)
    if any((g.dtype==torch.int16)!=stateful for g in gates):raise ValueError("Mixed signal contracts")
    clocks=torch.arange(t,device=device,dtype=torch.float64)
    lengths=torch.tensor([v.clocks for v in data],device=device)
    select_signals=torch.compile(state_signals,fullgraph=True,dynamic=False) if backend=='compile' else state_signals
    transition=torch.compile(step,fullgraph=True,dynamic=False) if backend=='compile' else step
    # All strategy/market computations are parallel on these axes; the time
    # loop preserves entry-dependent position semantics.
    for clock in range(1,t):
        price=prices[clock];previous=prices[clock-1];observed=observed_all[clock];membership=membership_all[clock-1]
        signals=all_signals[clock-1]
        held=position[...,0]>1e-12
        signals=select_signals(signals,held,clocks[clock-1]-opened,age_limits,stateful)
        swing=values[all_ids[clock,:,None,:],columns[None,:,None]]
        terminal=(clock==lengths-1)[:,None,None]
        old_position,old_aggregate=position,aggregate
        position,aggregate=transition(position,aggregate,price,previous,observed,membership,signals,swing,policy,
            clocks[clock],terminal,execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000.)
        opened=torch.where(~held&(position[...,0]>1e-12),clocks[clock],opened)
        active=clock<lengths
        position=torch.where(active[:,None,None,None],position,old_position)
        aggregate=torch.where(active[:,None,None],aggregate,old_aggregate)
        if progress is not None and (clock%256==0 or clock==t-1):progress(clock,t-1)
    if device.type=='cuda':torch.cuda.synchronize(device)
    result=[]
    for i,item in enumerate(data):
        q=position[i,...,0];realized=position[i,...,7].sum(-1)
        result.append(dict(net_pnl=realized,drawdown=aggregate[i,:,1],capital_dollar_seconds=aggregate[i,:,2],
            stop_risk_dollar_seconds=aggregate[i,:,3],filled_batches=aggregate[i,:,4],terminal_valid=(q<=1e-12).all(-1)&torch.isfinite(realized),
            inactivity_fraction=aggregate[i,:,7]/max(1,item.clocks-1),add_count=aggregate[i,:,5],reduce_count=aggregate[i,:,6],
            worst_position_pnl=torch.where(torch.isfinite(aggregate[i,:,8]),aggregate[i,:,8],0.),closed_positions=aggregate[i,:,9]))
    return result
