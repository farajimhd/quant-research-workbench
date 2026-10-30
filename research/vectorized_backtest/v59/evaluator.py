"""Protected float64 financial replay with live decisions and capped sizing."""
from dataclasses import dataclass
import math,torch
from .genome import STAGES
from .config import STATES
@dataclass(frozen=True)
class Execution:
    entry_dollars:float=1000.
    add_dollars:float=200.
    cost_bps:float=0.
    fee_per_share:float=0.
    minimum_order_fee:float=0.
    risk_limits_enabled:bool=True
    risk_protection:bool=True
    maximum_stop_distance:float=.20
    maximum_position_risk:float=300.
    minimum_order_dollars:float=0.
    price_mode:str='bid-ask'
    maximum_quote_age_seconds:float=1.
    @property
    def admission_distance(self):return self.maximum_stop_distance if self.risk_limits_enabled else 1.
    @property
    def admission_budget(self):return self.maximum_position_risk if self.risk_limits_enabled else float("inf")
    def validate(self):
        if type(self.risk_limits_enabled) is not bool:raise ValueError("Invalid risk admission switch")
        if self.price_mode!='bid-ask' or self.maximum_quote_age_seconds!=1.:raise ValueError('Strict V59 bid/ask execution required')
        if any(not math.isfinite(v) or v<0 for v in (self.entry_dollars,self.add_dollars,self.cost_bps,self.fee_per_share,self.minimum_order_fee)) or min(self.entry_dollars,self.add_dollars)<=0 or not math.isfinite(self.minimum_order_dollars) or self.minimum_order_dollars<0:
            raise ValueError('Invalid assumed-fill sizing/cost')
        if self.entry_dollars>1000 or self.add_dollars>200:raise ValueError('Entry/add caps are $1000/$200')
        if type(self.risk_protection) is not bool or not 0<self.maximum_stop_distance<=.20 or not math.isfinite(self.maximum_position_risk) or not 0<self.maximum_position_risk<=300:
            raise ValueError('V18 risk envelope: stop <=20%, position risk <=$300')
        return self


def step(position,aggregate,price,previous,observed,membership,signals,swing,policy,clock:torch.Tensor,terminal,entry_dollars:float,add_dollars:float,cost:float,fee_per_share:float=0.,minimum_order_fee:float=0.,risk_protection:bool=False,maximum_stop_distance:float=.20,maximum_position_risk:float=300.,ratios=None,minimum_order_dollars:float=0.,*,bid,ask,quote_valid):
    """[session,strategy,ticker] lanes; all signals predate the execution price.

    Position fields: quantity, average cost, stop, next target, high water,
    adds, last management clock, realized P&L, episode start P&L.
    Aggregate: peak equity, drawdown, capital seconds, risk seconds,
    entries, adds, reductions, inactive seconds, worst complete episode, episodes,
    profitable complete episodes after all costs.
    """
    q,avg,stop,target,water,adds,last,realized,episode=position.unbind(-1)
    dynamic_reduce=None
    if ratios is not None:
        safe=torch.where(torch.isfinite(ratios)&(ratios>=0)&(ratios<=1),ratios,0.)
        entry_dollars=entry_dollars*safe[...,0];add_dollars=add_dollars*safe[...,1];dynamic_reduce=safe[...,2]
    held=q>1e-12
    valid_quote=quote_valid&torch.isfinite(bid)&torch.isfinite(ask)&(bid>0)&(ask>=bid)
    buy_price=torch.where(valid_quote,ask,torch.zeros_like(ask))
    sell_price=torch.where(valid_quote,bid,torch.zeros_like(bid))
    tradable=observed&torch.isfinite(price)&(price>0)&valid_quote
    profit=previous/avg.clamp_min(1e-12)-1
    trail=(signals&16)!=0
    ratchet=held&trail&torch.isfinite(previous)
    water=torch.where(ratchet,torch.maximum(water,previous),water)
    stop=torch.where(ratchet,torch.maximum(stop,water*(1-policy[...,3])),stop)
    exit_rule=(signals&2)!=0
    terminal_price=torch.isfinite(price)&(price>0)&valid_quote
    exit_now=held&((terminal&terminal_price)|((exit_rule|(previous<=stop))&tradable))
    managed=(clock-last)>=policy[...,7]
    target_hit=held&(previous>=target)
    reduce_now=held&~exit_now&tradable&managed&(((signals&8)!=0)&(profit>=policy[...,5])|target_hit)
    fraction=policy[...,2] if dynamic_reduce is None else torch.where(target_hit,policy[...,2],dynamic_reduce)
    reduce_now=reduce_now&(fraction>0)
    sell=torch.where(exit_now,q,torch.where(reduce_now,q*fraction,torch.zeros_like(q)))
    execution_price=torch.where(torch.isfinite(price),price,torch.zeros_like(price))
    realized=realized+sell*(sell_price-avg)-sell*sell_price*cost
    realized-=torch.where(sell>0,(sell*fee_per_share).clamp_min(minimum_order_fee),torch.zeros_like(sell))
    remaining=(q-sell).clamp_min(0)
    closed=held&(remaining<=1e-12)
    complete_profit=realized-episode
    episode_worst=torch.where(closed,complete_profit,torch.full_like(q,float('inf'))).amin(-1)
    add_now=held&~exit_now&~reduce_now&tradable&managed&((signals&4)!=0)&(profit>=policy[...,4])&(adds<policy[...,6])
    entry_now=~held&~terminal&tradable&membership&((signals&1)!=0)
    add_now=add_now&(add_dollars>0)&(add_dollars>=minimum_order_dollars)
    initial=buy_price*(1-policy[...,0])
    swing_mode=policy[...,8]>0
    initial=torch.where(swing_mode,swing,initial)
    entry_now=entry_now&(entry_dollars>0)&(entry_dollars>=minimum_order_dollars)
    entry_now&=torch.isfinite(initial)&(initial>0)&(initial<buy_price)
    rejected_entry=torch.zeros_like(entry_now);rejected_add=torch.zeros_like(add_now)
    if risk_protection:
        safe_price=buy_price.clamp_min(1e-12)
        entry_q=entry_dollars/safe_price
        entry_risk=entry_q*(buy_price-initial).clamp_min(0)+2*(entry_q*fee_per_share).clamp_min(minimum_order_fee)+entry_dollars*cost+entry_q*initial.clamp_min(0)*cost
        safe_entry=((buy_price-initial)<=(maximum_stop_distance+1e-12)*safe_price)&(entry_risk<=maximum_position_risk)
        rejected_entry=entry_now&~safe_entry;entry_now&=safe_entry
        add_q=add_dollars/safe_price;prospective_q=remaining+add_q
        # Existing paid costs/net realized losses plus prospective buy and exit
        # costs. Realized gains do not subsidize a larger stop-risk budget.
        add_risk=(remaining*(avg-stop)+add_q*(buy_price-stop)).clamp_min(0)+(episode-realized).clamp_min(0)+(add_q*fee_per_share).clamp_min(minimum_order_fee)+add_dollars*cost+(prospective_q*fee_per_share).clamp_min(minimum_order_fee)+prospective_q*stop.clamp_min(0)*cost
        prospective_basis=remaining*avg+add_q*buy_price
        # Positive basis/quantity permit the same distance test without two
        # candidate x listing float64 divisions on every replay clock.
        safe_add=(add_risk<=maximum_position_risk)&(prospective_q*stop>=(1-maximum_stop_distance-1e-12)*prospective_basis)
        rejected_add=add_now&~safe_add;add_now&=safe_add
    # Scalar-only where() defaults to float32, including dollar fee arithmetic.
    dollars=entry_now.to(q.dtype)*entry_dollars+add_now.to(q.dtype)*add_dollars
    bought=dollars/buy_price.clamp_min(1e-12)
    quantity=remaining+bought
    # Sales and elapsed clocks never change the surviving shares' cost basis.
    # Repeated multiply/divide was drifting at exact zero-profit gates under
    # compilation. Entry basis is its actual fill; only additions reweight it.
    added_avg=avg+(bought/quantity.clamp_min(1e-12))*(buy_price-avg)
    new_avg=torch.where(entry_now,buy_price,torch.where(add_now,added_avg,
        torch.where(quantity>0,avg,torch.zeros_like(avg))))
    episode=torch.where(entry_now,realized,episode)
    realized=realized-dollars*cost
    realized-=torch.where(bought>0,(bought*fee_per_share).clamp_min(minimum_order_fee),torch.zeros_like(bought))
    stop=torch.where(entry_now,initial,stop)
    target=torch.where(entry_now,buy_price*(1+policy[...,1]),torch.where(target_hit&reduce_now,target+avg*policy[...,1],target))
    water=torch.where(entry_now,buy_price,water)
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
        turnover=aggregate[...,11]+(sell*sell_price+dollars).sum(-1)
        updated=torch.cat((updated,turnover.unsqueeze(-1)),-1)
    if aggregate.shape[-1]>12:
        distance=torch.where(quantity>0,(new_avg-stop).clamp_min(0)/new_avg.clamp_min(1e-12),0.).amax(-1)
        extra=torch.stack((torch.maximum(aggregate[...,12],position_risk.amax(-1)),torch.maximum(aggregate[...,13],distance),aggregate[...,14]+rejected_entry.sum(-1),aggregate[...,15]+rejected_add.sum(-1)),-1)
        updated=torch.cat((updated,extra),-1)
    return position,updated



def state_signals(signals,held,age,limits,stateful:bool):
    if stateful:signals=torch.where(held,signals>>8,signals)&255
    allowed=(age.clamp_min(0)[...,None]>=limits)|~held[...,None]
    mask=allowed[...,0].to(torch.int32)+2*allowed[...,1].to(torch.int32)+4*allowed[...,2].to(torch.int32)+8*allowed[...,3].to(torch.int32)+16*allowed[...,4].to(torch.int32)
    return signals&mask


class PopulationPrograms:
    def __init__(self,members,device='cpu',*,packed=None):
        from .network import NetworkPlan
        if any(m.routing for m in members):
            from .routing_plan import RoutingPlan
            self.plan=RoutingPlan(members,device)
        else:self.plan=NetworkPlan(members,device,packed=packed)
    def evaluate(self,data,**kwargs):return self.plan
    def close(self):self.plan.close()


@dataclass
class PreparedEvaluation:
    replay: object
    export: object


def replay_cohort(data,members,gates,*,execution=Execution(),backend='eager',progress=None,listing_width=None,episode_capacity=65536,trace=False,protected_kernel=True,capture_only=False,replay_block=32,execution_gate=None):
    """Independent sessions are dispatched by SessionLanes; no shared state."""
    if len(data)!=1 or len(gates)!=1:raise ValueError('One independent session per lane required')
    execution.validate();item=data[0];plan=gates[0]
    if item.quote_coverage['maximum_age_seconds']!=execution.maximum_quote_age_seconds:raise ValueError('Quote execution identity changed')
    device=item.device;b=len(members);s=1;t=item.clocks
    u=len(item.listing_ids) if listing_width is None else listing_width
    if capture_only and not (backend=='compile' and device.type=='cuda'):
        raise ValueError('Prepared evaluation requires compiled CUDA replay')
    if not 1<=replay_block<=256:raise ValueError('Invalid bounded replay block')
    if u<len(item.listing_ids):raise ValueError('Cannot truncate listings')
    from torch.nn.functional import pad
    from .episodes import Episodes
    from research.vectorized_backtest.v42.swing_projection import entry_swings
    episodes=Episodes(1,b,episode_capacity,device,max_listings=u,trace=trace)
    # Include the live one-clock neuron values and provenance, in addition to
    # the unchanged financial buffers. Capacity is never reduced for admission.
    required=episodes.profits.numel()*(32 if trace else 16)+b*u*1024+plan.total_nodes*u*5+b*u*len(STATES)*4+item.clocks*u*32
    plan.prepare(b,u);plan.reset()
    if device.type=='cuda' and required>torch.cuda.mem_get_info(device)[0]*.8:raise MemoryError('Neural replay exceeds physical GPU headroom')
    position=torch.zeros((1,b,u,9),dtype=torch.float64,device=device)
    aggregate=torch.zeros((1,b,16),dtype=torch.float64,device=device);aggregate[...,8]=float('inf')
    policy=torch.tensor([[getattr(m.policy,k) for k in ('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit','maximum_adds','cooldown','swing_left')] for m in members],dtype=torch.float64,device=device)[None,:,None]
    ticker_state=torch.zeros((1,b,u,2),dtype=torch.float64,device=device)
    opened=torch.zeros((1,b,u),dtype=torch.float64,device=device)
    prices=pad(item.tensors['mark'][:t],(0,u-len(item.listing_ids)),value=float('nan'))[:,None,None]
    bids=pad(item.tensors['bid'][:t],(0,u-len(item.listing_ids)),value=0.)[:,None,None]
    asks=pad(item.tensors['ask'][:t],(0,u-len(item.listing_ids)),value=0.)[:,None,None]
    quote_valid=pad(item.tensors['quote_valid'][:t],(0,u-len(item.listing_ids)),value=False)[:,None,None]
    observed=pad(item.tensors['observed'][:t],(0,u-len(item.listing_ids)),value=False)[:,None,None]
    membership=pad(item.tensors['membership'][:t],(0,u-len(item.listing_ids)),value=False)[:,None,None]
    routed=any(m.routing for m in members)
    values,columns,ids=entry_swings(item,plan.leaves if routed else members);ids=pad(ids[:t].to(torch.int64),(0,u-len(item.listing_ids)),value=0)[:,None]
    limits=torch.tensor([[m.minimum_age.get(k,0) for k in STAGES] for m in members],device=device,dtype=torch.float64)[None,:,None]
    if routed:
        episodes.owner=plan.owner[None]
        plan.leaf_columns=columns
        columns=torch.empty((b,u),device=device,dtype=columns.dtype)
        policy=policy.expand(1,b,u,9).clone();limits=limits.expand(1,b,u,5).clone()
        plan.bind(policy,limits,columns)
    lengths=torch.tensor([t],device=device);signals=torch.zeros((1,b,u),device=device,dtype=torch.int16)
    from .ownership import action_mask
    owner_actions=action_mask(item,members,u) if any(getattr(m,'case_owner',None) is not None for m in members) else None
    if hasattr(plan,'members'):
        from .ownership import bind_neural_lanes
        bind_neural_lanes(plan,item,members)
    def provide(index,p,o):
        previous=prices.index_select(0,index.reshape(1)).squeeze(0)
        plan.signals(item,index,p,o,previous,out=signals)
        if owner_actions is not None:signals.bitwise_and_(owner_actions)
    if backend=='compile' and device.type=='cuda':
        from .replay_graph import replay
        prepared=replay(position,aggregate,opened,policy,prices,observed,membership,signals,values,ids,columns,limits,lengths,False,
               execution,step,state_signals,progress,block=replay_block,episodes=episodes,protected_kernel=protected_kernel,signal_provider=provide,network_plan=plan,capture_only=capture_only,ticker_state=ticker_state,bids=bids,asks=asks,quote_valid=quote_valid,execution_gate=execution_gate)
        if capture_only:
            return PreparedEvaluation(prepared,lambda: _export_result(position,aggregate,episodes,t,ticker_state))
    else:
        for clock in range(1,t):
            index=torch.tensor(clock-1,device=device);provide(index,position,opened);held=position[...,0]>1e-12
            selected=state_signals(signals,held,clock-1-opened,limits,False)
            swing=values[ids[clock,:,None,:],columns[None] if routed else columns[None,:,None]]
            before=position
            position,aggregate=step(position,aggregate,prices[clock],prices[clock-1],observed[clock],membership[clock-1],selected,swing,policy,
                torch.tensor(float(clock),device=device),torch.tensor(clock==t-1,device=device),execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000,execution.fee_per_share,execution.minimum_order_fee,
                execution.risk_protection,execution.admission_distance,execution.admission_budget,plan.ratios,execution.minimum_order_dollars,bid=bids[clock],ask=asks[clock],quote_valid=quote_valid[clock])
            from .ticker_selection import update_equity
            update_equity(ticker_state,position,prices[clock],torch.tensor(True,device=device))
            from .network import outcome_values
            plan.feedback.copy_(outcome_values(before,position,asks[clock],selected,plan.ratios,plan.feedback))
            episodes.append(before,position,torch.tensor([True],device=device),opened,torch.tensor(float(clock),device=device))
            opened=torch.where(~held&(position[...,0]>1e-12),float(clock),opened)
            if progress and (clock%256==0 or clock==t-1):progress(clock,t-1)
    if device.type=='cuda':torch.cuda.synchronize(device)
    return _export_result(position,aggregate,episodes,t,ticker_state)


def _export_result(position,aggregate,episodes,t,ticker_state):
    episode_results=episodes.export();q=position[0,...,0];realized=position[0,...,7].sum(-1)
    held=(q>1e-12).cpu().numpy()
    open_cash=(position[0,...,7]-position[0,...,8]).detach().cpu().numpy()
    open_ids=[row.nonzero()[0].tolist() for row in held]
    open_profits=[open_cash[i,row].tolist() for i,row in enumerate(held)]
    result=dict(ticker_drawdown=ticker_state[0,...,1],open_episode_listings=open_ids,open_episode_realized=open_profits,net_pnl=realized,drawdown=aggregate[0,:,1],capital_dollar_seconds=aggregate[0,:,2],stop_risk_dollar_seconds=aggregate[0,:,3],
        filled_batches=aggregate[0,:,4],terminal_valid=(q<=1e-12).all(-1)&torch.isfinite(realized),unliquidated_positions=(q>1e-12).sum(-1),inactivity_fraction=aggregate[0,:,7]/max(1,t-1),
        add_count=aggregate[0,:,5],reduce_count=aggregate[0,:,6],worst_position_pnl=torch.where(torch.isfinite(aggregate[0,:,8]),aggregate[0,:,8],0.),
        closed_positions=aggregate[0,:,9],winning_positions=aggregate[0,:,10],turnover=aggregate[0,:,11],maximum_position_risk=aggregate[0,:,12],maximum_stop_distance=aggregate[0,:,13],
        risk_rejected_entries=aggregate[0,:,14],risk_rejected_adds=aggregate[0,:,15],**episode_results[0])
    return [result]
