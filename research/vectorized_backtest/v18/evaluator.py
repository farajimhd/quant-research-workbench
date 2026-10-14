"""Vectorized position lanes; chronology is required only inside each lane."""
from dataclasses import dataclass
import math
import torch
from .program import TorchPrograms
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
from .features import CATALOG,BASE
from .genome import STAGES,effective_rules

@dataclass(frozen=True)
class Execution:
    entry_dollars:float=1000.
    add_dollars:float=200.
    cost_bps:float=0.
    fee_per_share:float=0.
    minimum_order_fee:float=0.
    risk_protection:bool=True
    maximum_stop_distance:float=.20
    maximum_position_risk:float=300.
    def validate(self):
        if any(not math.isfinite(v) or v<0 for v in (self.entry_dollars,self.add_dollars,self.cost_bps,self.fee_per_share,self.minimum_order_fee)) or min(self.entry_dollars,self.add_dollars)<=0:
            raise ValueError('Invalid assumed-fill sizing/cost')
        if type(self.risk_protection) is not bool or not 0<self.maximum_stop_distance<=.20 or not math.isfinite(self.maximum_position_risk) or not 0<self.maximum_position_risk<=300:
            raise ValueError('V18 risk envelope: stop <=20%, position risk <=$300')
        return self


def step(position,aggregate,price,previous,observed,membership,signals,swing,policy,clock:torch.Tensor,terminal,entry_dollars:float,add_dollars:float,cost:float,fee_per_share:float=0.,minimum_order_fee:float=0.,risk_protection:bool=False,maximum_stop_distance:float=.20,maximum_position_risk:float=300.):
    """[session,strategy,ticker] lanes; all signals predate the execution price.

    Position fields: quantity, average cost, stop, next target, high water,
    adds, last management clock, realized P&L, episode start P&L.
    Aggregate: peak equity, drawdown, capital seconds, risk seconds,
    entries, adds, reductions, inactive seconds, worst complete episode, episodes,
    profitable complete episodes after all costs.
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
    realized-=torch.where(sell>0,(sell*fee_per_share).clamp_min(minimum_order_fee),torch.zeros_like(sell))
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
    rejected_entry=torch.zeros_like(entry_now);rejected_add=torch.zeros_like(add_now)
    if risk_protection:
        safe_price=execution_price.clamp_min(1e-12)
        entry_q=entry_dollars/safe_price
        entry_risk=entry_q*(execution_price-initial).clamp_min(0)+2*(entry_q*fee_per_share).clamp_min(minimum_order_fee)+entry_dollars*cost+entry_q*initial.clamp_min(0)*cost
        safe_entry=((execution_price-initial)/safe_price<=maximum_stop_distance+1e-12)&(entry_risk<=maximum_position_risk)
        rejected_entry=entry_now&~safe_entry;entry_now&=safe_entry
        add_q=add_dollars/safe_price;prospective_q=remaining+add_q
        # Existing paid costs/net realized losses plus prospective buy and exit
        # costs. Realized gains do not subsidize a larger stop-risk budget.
        add_risk=(remaining*(avg-stop)+add_q*(execution_price-stop)).clamp_min(0)+(episode-realized).clamp_min(0)+(add_q*fee_per_share).clamp_min(minimum_order_fee)+add_dollars*cost+(prospective_q*fee_per_share).clamp_min(minimum_order_fee)+prospective_q*stop.clamp_min(0)*cost
        prospective_avg=(remaining*avg+add_q*execution_price)/prospective_q.clamp_min(1e-12)
        safe_add=(add_risk<=maximum_position_risk)&((prospective_avg-stop)/prospective_avg.clamp_min(1e-12)<=maximum_stop_distance+1e-12)
        rejected_add=add_now&~safe_add;add_now&=safe_add
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
    realized-=torch.where(bought>0,(bought*fee_per_share).clamp_min(minimum_order_fee),torch.zeros_like(bought))
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
    if risk_protection:
        position_risk=torch.where(quantity>0,quantity*(new_avg-stop).clamp_min(0)+(episode-realized).clamp_min(0)+(quantity*fee_per_share).clamp_min(minimum_order_fee)+quantity*stop.clamp_min(0)*cost,0.)
        risk=position_risk.sum(-1)
    else: risk=(quantity*(execution_price-stop).clamp_min(0)).sum(-1)
    fields=(peak,dd,aggregate[...,2]+capital,aggregate[...,3]+risk,
        aggregate[...,4]+entry_now.sum(-1),aggregate[...,5]+add_now.sum(-1),aggregate[...,6]+reduce_now.sum(-1),
        aggregate[...,7]+(~(quantity>0).any(-1)).to(equity.dtype),torch.minimum(aggregate[...,8],episode_worst),
        aggregate[...,9]+closed.sum(-1))
    updated=torch.stack(fields,-1)
    if aggregate.shape[-1]>10:
        wins=aggregate[...,10]+(closed&(complete_profit>0)).sum(-1)
        updated=torch.cat((updated,wins.unsqueeze(-1)),-1)
    if aggregate.shape[-1]>11:
        turnover=aggregate[...,11]+(sell*execution_price+dollars).sum(-1)
        updated=torch.cat((updated,turnover.unsqueeze(-1)),-1)
    if aggregate.shape[-1]>12:
        distance=torch.where(quantity>0,(new_avg-stop).clamp_min(0)/new_avg.clamp_min(1e-12),0.).amax(-1)
        extra=torch.stack((torch.maximum(aggregate[...,12],position_risk.amax(-1)),torch.maximum(aggregate[...,13],distance),aggregate[...,14]+rejected_entry.sum(-1),aggregate[...,15]+rejected_add.sum(-1)),-1)
        updated=torch.cat((updated,extra),-1)
    return position,updated


class PopulationPrograms:
    """Pack once per candidate batch; reuse the same tensors across sessions."""
    def __init__(self,members,device='cpu'):
        for member in members:member.validate()
        self.members=members;self.device=torch.device(device)
        from .rule_plan import prune
        self.programs={s:TorchPrograms([prune(effective_rules(m)[s]) for m in members],CATALOG,self.device) for s in STAGES}
        self.plan=None
    def evaluate(self,data,chunk=2048,listing_batch=None,workspace_gib=None,maximum_gate_gib=2.,progress=None):
        if listing_batch is None:
            # A disk tile's four listings are not a GPU compute constraint.
            listing_batch=min(32,max(4,4096//len(self.members))) if self.device.type=='cuda' else 4
        if self.device.type=='cuda':
            from .rule_plan import RulePlan
            if self.plan is None:self.plan=RulePlan(self.members,self.device)
            self.plan.workspace_gib=workspace_gib
            return self.plan.evaluate(data,self.members,chunk,listing_batch,maximum_gate_gib,progress=progress)
        b=len(self.members);u=len(data.listing_ids)
        if b*data.clocks*u>maximum_gate_gib*1024**3:raise MemoryError('V7 rule gates exceed declared budget')
        gates=torch.zeros((b,data.clocks,u),dtype=torch.uint8,device=self.device)
        width=max(v.width for v in self.programs.values())
        estimate=(chunk+119)*(len(CATALOG)*5+b*(width*20+64))*listing_batch
        if workspace_gib is None:
            workspace_gib=torch.cuda.mem_get_info(self.device)[0]*.8/1024**3 if self.device.type=='cuda' else 2.
        if estimate>workspace_gib*1024**3:raise MemoryError('V7 rule workspace exceeds declared budget')
        from .io_pipeline import feature_tiles
        requests=((begin,min(data.clocks,begin+chunk),list(range(first,min(u,first+listing_batch))))
                  for first in range(0,u,listing_batch) for begin in range(0,data.clocks,chunk))
        for (begin,end,listings),(inputs,known) in feature_tiles(data,requests):
            first=listings[0];warm=max(0,begin-119)
            for bit,stage in enumerate(STAGES):
                signal,mask=self.programs[stage](inputs,known)
                gates[:,begin:end,first:first+len(listings)]|=((signal[:,:,begin-warm:]!=0)&mask[:,:,begin-warm:]).transpose(1,2).to(torch.uint8)*(1<<bit)
        return gates

    def close(self):
        if self.plan is not None:self.plan.close()


def state_signals(signals,held,age,limits,stateful:bool):
    if stateful:signals=torch.where(held,signals>>8,signals)&255
    allowed=(age.clamp_min(0)[...,None]>=limits)|~held[...,None]
    mask=allowed[...,0].to(torch.int32)+2*allowed[...,1].to(torch.int32)+4*allowed[...,2].to(torch.int32)+8*allowed[...,3].to(torch.int32)+16*allowed[...,4].to(torch.int32)
    return signals&mask


def replay_cohort(data,members,gates,*,execution=Execution(),backend='eager',progress=None,listing_width=None, episode_capacity=65536,trace=False):
    """One batched position engine across independent sessions and strategies.

    No bid/ask, spread, volume-capacity, cash, or pending-order tensors enter
    this function. GPU compilation specializes the small transition kernel.
    """
    execution.validate()
    if not data or len(data)!=len(gates) or backend not in ('eager','compile'):raise ValueError('Invalid replay cohort')
    device=data[0].device;b=len(members);s=len(data);u=max(len(v.listing_ids) for v in data);t=max(v.clocks for v in data)
    if listing_width is not None:
        if listing_width<u:raise ValueError('Replay listing width truncates a session')
        u=listing_width
    if any(v.device!=device for v in data):raise ValueError('Cohort devices differ')
    from .episodes import Episodes
    episodes=Episodes(s,b,episode_capacity,device,max_listings=u,trace=trace)
    position=torch.zeros((s,b,u,9),dtype=torch.float64,device=device)
    aggregate=torch.zeros((s,b,16 if execution.risk_protection else 12),dtype=torch.float64,device=device);aggregate[...,8]=float('inf')
    policy=torch.tensor([[m.policy.stop_fraction,m.policy.target_fraction,m.policy.reduce_fraction,m.policy.trail_fraction,
        m.policy.add_minimum_profit,m.policy.reduce_minimum_profit,m.policy.maximum_adds,m.policy.cooldown,m.policy.swing_left] for m in members],dtype=torch.float64,device=device)[None,:,None,:]
    from .swing_projection import entry_swings
    swing_banks=[entry_swings(v,members) for v in data]
    # Pack the cohort once. The chronological loop has no per-session host
    # dispatch, tensor construction, or policy repacking.
    from torch.nn.functional import pad
    required=t*s*b*u*gates[0].element_size()+sum(v.numel()*v.element_size() for v,c,h in swing_banks)
    required+=t*s*u*40+s*b*u*9*8
    if device.type=='cuda':
        free,_=torch.cuda.mem_get_info(device)
        if required>free*.8:raise MemoryError('Packed V7 cohort exceeds GPU headroom')
    prices=torch.stack([pad(v.tensors['mark'],(0,u-len(v.listing_ids),0,t-v.clocks),value=float('nan')) for v in data],1)[:,:,None]
    observed_all=torch.stack([pad(v.tensors['observed'],(0,u-len(v.listing_ids),0,t-v.clocks),value=False) for v in data],1)[:,:,None]
    membership_all=torch.stack([pad(v.tensors['membership'],(0,u-len(v.listing_ids),0,t-v.clocks),value=False) for v in data],1)[:,:,None]
    all_signals=torch.stack([pad(g,(0,u-len(v.listing_ids),0,t-v.clocks)) for v,g in zip(data,gates)],1).permute(2,1,0,3)
    values=torch.cat([bank for bank,columns,history_ids in swing_banks],0);columns=swing_banks[0][1]
    ids=[];offset=0
    for item,(bank,_,history_ids) in zip(data,swing_banks):
        ids.append(pad(history_ids.to(torch.int64)+offset,(0,u-len(item.listing_ids),0,t-item.clocks),value=offset))
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
    if backend=='compile' and device.type=='cuda':
        from .replay_graph import replay
        replay(position,aggregate,opened,policy,prices,observed_all,membership_all,
            all_signals,values,all_ids,columns,age_limits,lengths,stateful,execution,
            step,state_signals,progress,episodes=episodes)
    else:
        for clock in range(1,t):
            price=prices[clock];previous=prices[clock-1];observed=observed_all[clock];membership=membership_all[clock-1]
            signals=all_signals[clock-1]
            held=position[...,0]>1e-12
            signals=select_signals(signals,held,clocks[clock-1]-opened,age_limits,stateful)
            swing=values[all_ids[clock,:,None,:],columns[None,:,None]]
            terminal=(clock==lengths-1)[:,None,None]
            old_position,old_aggregate=position,aggregate
            position,aggregate=transition(position,aggregate,price,previous,observed,membership,signals,swing,policy,
                clocks[clock],terminal,execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000.,execution.fee_per_share,execution.minimum_order_fee,execution.risk_protection,execution.maximum_stop_distance,execution.maximum_position_risk)
            episodes.append(old_position,position,clock<lengths,opened,clocks[clock])
            opened=torch.where(~held&(position[...,0]>1e-12),clocks[clock],opened)
            active=clock<lengths
            position=torch.where(active[:,None,None,None],position,old_position)
            aggregate=torch.where(active[:,None,None],aggregate,old_aggregate)
            if progress is not None and (clock%256==0 or clock==t-1):progress(clock,t-1)
    if device.type=='cuda':torch.cuda.synchronize(device)
    episode_results=episodes.export()
    result=[]
    for i,item in enumerate(data):
        q=position[i,...,0];realized=position[i,...,7].sum(-1)
        result.append(dict(net_pnl=realized,drawdown=aggregate[i,:,1],capital_dollar_seconds=aggregate[i,:,2],
            stop_risk_dollar_seconds=aggregate[i,:,3],filled_batches=aggregate[i,:,4],terminal_valid=(q<=1e-12).all(-1)&torch.isfinite(realized),
            inactivity_fraction=aggregate[i,:,7]/max(1,item.clocks-1),add_count=aggregate[i,:,5],reduce_count=aggregate[i,:,6],
            worst_position_pnl=torch.where(torch.isfinite(aggregate[i,:,8]),aggregate[i,:,8],0.),closed_positions=aggregate[i,:,9],
            winning_positions=aggregate[i,:,10],turnover=aggregate[i,:,11],**episode_results[i]))
        if execution.risk_protection:
            result[-1].update(maximum_position_risk=aggregate[i,:,12],maximum_stop_distance=aggregate[i,:,13],risk_rejected_entries=aggregate[i,:,14],risk_rejected_adds=aggregate[i,:,15])
    return result
