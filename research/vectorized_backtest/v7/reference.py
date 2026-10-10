"""Independent NumPy ledger oracle for qualification, never search execution."""
import numpy as np

def replay_reference(data,members,gates,execution):
    b=len(members);u=len(data.listing_ids);shape=(b,u)
    q=np.zeros(shape);basis=np.zeros(shape);stop=np.zeros(shape);target=np.zeros(shape)
    water=np.zeros(shape);adds=np.zeros(shape);last=np.zeros(shape);realized=np.zeros(shape);episode=np.zeros(shape)
    opened=np.zeros(shape)
    peak=np.zeros(b);dd=np.zeros(b);capital=np.zeros(b);risk=np.zeros(b);entries=np.zeros(b)
    add_count=np.zeros(b);reductions=np.zeros(b);inactive=np.zeros(b);worst=np.full(b,np.inf);closed_count=np.zeros(b)
    policy=lambda name:np.array([getattr(m.policy,name) for m in members])[:,None]
    cost=execution.cost_bps/10000.
    price=data.host_tensors['mark'].numpy();observed=data.host_tensors['observed'].numpy();membership=data.host_tensors['membership'].numpy()
    bank,columns=data.swing_bank(members);bank=bank.cpu().numpy();columns=columns.cpu().numpy()
    ids=data.host_tensors['history_ids'].numpy();gates=np.asarray(gates)
    for clock in range(1,data.clocks):
        current=price[clock][None];previous=price[clock-1][None];signal=gates[:,clock-1,:]
        held=q>1e-12;tradable=observed[clock][None]&np.isfinite(current)&(current>0)
        if gates.dtype==np.int16:signal=np.where(held,signal>>8,signal)&255
        age=np.maximum(clock-1-opened,0)
        for bit,stage in enumerate(("entry","exit","add","reduce","trail")):
            limit=np.array([m.minimum_age.get(stage,0) for m in members])[:,None]
            signal=signal&np.where((age>=limit)|~held,255,255^(1<<bit))
        profit=previous/np.maximum(basis,1e-12)-1
        ratchet=held&((signal&16)!=0)&np.isfinite(previous)
        water=np.where(ratchet,np.maximum(water,previous),water)
        stop=np.where(ratchet,np.maximum(stop,water*(1-policy('trail_fraction'))),stop)
        terminal=clock==data.clocks-1
        exit_now=held&((terminal&np.isfinite(current)&(current>0))|((((signal&2)!=0)|(previous<=stop))&tradable))
        manageable=clock-last>=policy('cooldown');target_hit=held&(previous>=target)
        reduce=held&~exit_now&tradable&manageable&((((signal&8)!=0)&(profit>=policy('reduce_minimum_profit')))|target_hit)
        sell=np.where(exit_now,q,np.where(reduce,q*policy('reduce_fraction'),0.))
        px=np.where(np.isfinite(current),current,0.)
        realized+=sell*(px-basis)-sell*px*cost
        remaining=np.maximum(q-sell,0.);closed=held&(remaining<=1e-12)
        worst=np.minimum(worst,np.where(closed,realized-episode,np.inf).min(axis=1));closed_count+=closed.sum(axis=1)
        add=held&~exit_now&~reduce&tradable&manageable&((signal&4)!=0)&(profit>=policy('add_minimum_profit'))&(adds<policy('maximum_adds'))
        entry=~held&(not terminal)&tradable&membership[clock-1][None]&((signal&1)!=0)
        initial=px*(1-policy('stop_fraction'))
        initial=np.where(policy('swing_left')>0,bank[ids[clock][None],columns[:,None]],initial)
        entry&=np.isfinite(initial)&(initial>0)&(initial<px)
        dollars=np.where(entry,execution.entry_dollars,np.where(add,execution.add_dollars,0.))
        bought=dollars/np.maximum(px,1e-12);q=remaining+bought;old_basis=basis
        updated_basis=basis+(bought/np.maximum(q,1e-12))*(px-basis)
        basis=np.where(entry,px,np.where(add,updated_basis,np.where(q>0,basis,0.)))
        opened=np.where(entry,clock,opened)
        episode=np.where(entry,realized,episode);realized-=dollars*cost
        stop=np.where(entry,initial,stop)
        target=np.where(entry,px*(1+policy('target_fraction')),np.where(target_hit&reduce,target+old_basis*policy('target_fraction'),target))
        water=np.where(entry,px,water);adds=np.where(entry,0.,adds+add)
        last=np.where(entry|add|reduce,clock,last)
        equity=(realized+q*(px-basis)).sum(axis=1);peak=np.maximum(peak,equity);dd=np.maximum(dd,peak-equity)
        capital+=(q*px).sum(axis=1);risk+=(q*np.maximum(px-stop,0.)).sum(axis=1)
        entries+=entry.sum(axis=1);add_count+=add.sum(axis=1);reductions+=reduce.sum(axis=1);inactive+=(~(q>0).any(axis=1))
    pnl=realized.sum(axis=1)
    return dict(net_pnl=pnl,drawdown=dd,capital_dollar_seconds=capital,stop_risk_dollar_seconds=risk,filled_batches=entries,
        terminal_valid=(q<=1e-12).all(axis=1)&np.isfinite(pnl),inactivity_fraction=inactive/max(1,data.clocks-1),
        add_count=add_count,reduce_count=reductions,worst_position_pnl=np.where(np.isfinite(worst),worst,0.),closed_positions=closed_count)
