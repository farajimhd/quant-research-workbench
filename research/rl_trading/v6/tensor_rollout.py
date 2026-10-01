"""Real V6 policy collection with GPU-resident approximate broker state.

Uses a versioned one-proposal-per-second cadence. Candle and action histories
remain chronological; only listing arithmetic and execution events are batched.
The ranker's identity control plane is still CPU based and measured separately.
"""
from dataclasses import dataclass
import numpy as np
import torch
from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.rollout import (PolicyFrame,PolicyStep,_initialize,
    _advance,_remember,_distribution)
from research.rl_trading.v6.tensor_broker import TensorOutcomes,compact_outcomes


def empty_outcomes(device):
    integer=torch.empty(0,device=device,dtype=torch.long)
    value=torch.empty(0,device=device)
    return TensorOutcomes(integer,integer,value,value,value)


def join_outcomes(groups,device):
    if not groups:
        return empty_outcomes(device)
    combined=TensorOutcomes(*(torch.cat([getattr(g,name) for g in groups]) for name in
        ('listing','action','requested_fraction','filled_fraction','net_over_equity',
         'shares','price','fee','clock','net_pnl','position_closed')),
        active=torch.cat([g.active for g in groups]) if groups[0].active is not None else None)
    return compact_outcomes(combined)


@dataclass
class TensorCollection:
    frames: list
    steps: list
    summary: dict
    bootstrap: torch.Tensor
    equity: torch.Tensor
    clocks: list


@torch.no_grad()
def collect_tensor_session(policy,session,broker,buckets,*,device,
                           max_clocks=None,progress_callback=None,deterministic=False):
    """Bucket iterator yields certified, consecutive 100 ms execution tensors.

    It is private to the broker; policy receives only completed candle rows,
    causal account observations and actual execution outcomes. Environment
    has no autograd graph. Truncated diagnostic rollouts retain next-state value.
    """
    policy.eval();state,memory=_initialize(policy,session,device)
    events=iter(session.candle_events());event=next(events,None)
    if event is None:
        raise ValueError('No certified actual candles')
    real_end=bounds(session.day)[1]
    end=real_end if max_clocks is None else min(real_end,event.close_us+max_clocks*1_000_000)
    if max_clocks is not None and max_clocks<2:
        raise ValueError('Diagnostic needs at least two clocks')
    execution=iter(buckets);frames=[];steps=[];equity=[];clocks=[]
    broker.clock_us=event.close_us
    previous=broker.equity();penalty=broker.shaping.clone()
    value=broker.cash.float().new_zeros(())
    for clock in range(event.close_us,end+1,1_000_000):
        groups=[]
        while broker.clock_us<clock:
            b=next(execution,None)
            if b is None or b.clock_us>clock:
                raise ValueError('Execution tape missing required 100 ms boundary')
            groups.append(broker.advance(b,dense=True))
        outcomes=join_outcomes(groups,device)
        memory=_remember(policy,state,memory,outcomes)
        if event is not None and event.close_us==clock:
            indices,rows=event.listing_index,event.bank_row;event=next(events,None)
        else:
            indices=rows=np.empty(0,dtype=np.int64)
        frame=PolicyFrame(clock,indices,rows,outcomes)
        scalar=_advance(policy,state,session,frame,device)
        valid=scalar[:,SCALAR_NAMES.index('bar_price_valid')]==1
        valid_index=torch.as_tensor(indices[valid],device=device,dtype=torch.long)
        price=torch.as_tensor(np.exp(scalar[valid,SCALAR_NAMES.index('log_close')].astype(np.float64)),device=device)
        broker.update_marks(valid_index,price,clock)
        eq=broker.equity()
        equity.append(eq.clone());clocks.append(clock)
        if clock==real_end:
            broker.terminal_penalty()
        if steps:
            steps[-1].reward+=(eq-previous)/broker.config.initial_cash-(broker.shaping-penalty)
            steps[-1].elapsed+=1.
        previous=eq.clone();penalty=broker.shaping.clone()
        changed=torch.zeros(broker.n,device=device,dtype=torch.bool)
        changed[valid_index]=True
        obs=broker.observe(clock,changed)
        step=PolicyStep(obs,(),0,0.,0.,0.)
        dist,value=_distribution(policy,state,memory,step,indices,scalar,device)
        if clock==end:
            if end==real_end and steps:
                steps[-1].terminal=True
            frames.append(frame);break
        if clock==real_end-1_000_000:
            broker.force_exit()
        else:
            if deterministic:
                token=dist.logits.argmax();latent=dist.locations.gather(0,token.reshape(1)).squeeze(0)
                kind=torch.where((token>0)&(token<=broker.n),1,
                    torch.where(token>=1+broker.n+obs.held_index.numel(),2,0))
                parameter=torch.where(kind==1,latent.sigmoid(),torch.where(kind==2,torch.nn.functional.softplus(latent),0.))
                likelihood=dist.tensor_log_prob(token,latent)
            else:
                token,latent,parameter,likelihood=dist.sample_tensor()
            step.token=token;step.latent=latent;step.old_log_prob=likelihood;step.old_value=value
            step.immediate_outcome=broker.submit(token,parameter,obs.held_index,clock)
            memory=_remember(policy,state,memory,step.immediate_outcome)
            frame.steps.append(step);steps.append(step)
        frames.append(frame)
        if progress_callback:
            # Callback chooses its own bounded logging frequency; avoids host
            # scalar transfer per clock unless explicitly requested by caller.
            progress_callback(clock,len(steps))
    state.detach()
    summary=broker.summary();summary['policy_steps']=len(steps)
    summary['decision_cadence']='one_proposal_per_second'
    marked=torch.stack(equity)
    delta=marked-torch.cat((marked.new_tensor([broker.config.initial_cash]),marked[:-1]))
    session_start=bounds(session.day)[0]
    regular=session_start+19_800_000_000;after=session_start+43_200_000_000
    clock_array=np.asarray(clocks)
    for name,mask in (('premarket',clock_array<regular),
                      ('regular',(clock_array>=regular)&(clock_array<after)),
                      ('after_hours',clock_array>=after)):
        summary[f'{name}_net_profit']=float(delta[torch.as_tensor(mask,device=device)].sum().cpu())
    summary['maximum_drawdown_dollars']=float((torch.cummax(marked,0).values-marked).max().cpu())
    return TensorCollection(frames,steps,summary,value.detach() if end<real_end else value.new_zeros(()),
        marked,clocks)
