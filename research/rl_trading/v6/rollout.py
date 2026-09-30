"""Chronological V6 policy collection and bounded-BPTT PPO reconstruction.

Stores compact actions/account snapshots and references existing bank rows;
never stores overlapping feature windows or teacher future labels in RL.
"""
from dataclasses import dataclass, field
from itertools import islice
import math
import numpy as np
import torch
from torch.distributions import Categorical
from research.rl_trading.v6.candle_stream import SparseCandleState, seed_previous_session
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.eligibility import causal_enter_mask
from research.rl_trading.v6.actor_critic import elapsed_gae, clipped_ppo_loss


@dataclass
class PolicyStep:
    observation: object
    pending_indices: tuple
    token: int
    latent: float
    old_log_prob: float
    old_value: float
    immediate_outcome: object = None
    reward: float = 0.
    elapsed: float = 0.
    terminal: bool = False
    blocked_indices: tuple = ()  # Causal mask retained for exact PPO rebuild.


@dataclass
class PolicyFrame:
    clock: int
    indices: np.ndarray
    rows: np.ndarray
    outcomes: tuple
    steps: list = field(default_factory=list)


def _initialize(policy, session, device):
    state = SparseCandleState.empty(policy.encoder,len(session.listings),device=device,dtype=torch.float32)
    seed_previous_session(state,policy.encoder,session.listings,session.previous)
    policy.reset_market(len(session.listings))
    memory = policy.initial_action_state(device=device,dtype=torch.float32)
    return state,memory


def _remember(policy,state,memory,outcomes):
    for outcome in outcomes:
        embedding = state.embeddings()[outcome.listing]
        memory = policy.remember_execution(memory,embedding,action=outcome.action,
            requested_fraction=embedding.new_tensor(outcome.requested_fraction),
            filled_fraction=embedding.new_tensor(outcome.filled_fraction),
            realized_net_over_equity=embedding.new_tensor(outcome.net_over_equity))
    return memory


def _advance(policy,state,session,frame,device):
    scalar_np = np.asarray(session.bank.scalar[frame.rows])
    scalar = torch.from_numpy(scalar_np.copy()).to(device)
    levels = torch.from_numpy(np.asarray(session.bank.levels[frame.rows]).copy()).to(device)
    indices = torch.as_tensor(frame.indices,dtype=torch.long,device=device)
    state.advance(policy.encoder,indices,scalar,levels)
    policy.observe_market(state,frame.clock,frame.indices,scalar_np)
    return scalar_np


def _distribution(policy,state,memory,step,indices,scalar,device):
    obs = step.observation
    pending = np.asarray(step.pending_indices,dtype=np.int64)
    enter = causal_enter_mask(len(state.encoded),indices,scalar,
        cash=float(obs.account[0]),reserved_cash=float(obs.account[5]),
        held_index=obs.held_index,pending_index=pending)
    if step.blocked_indices:
        enter[np.asarray(step.blocked_indices,dtype=np.int64)] = False
    policy.set_pending(step.pending_indices)
    tensor = lambda x,dtype=None: torch.as_tensor(x,dtype=dtype,device=device)
    return policy.distribution_and_value(state.embeddings(),tensor(obs.account),
        tensor(obs.held_index,torch.long),tensor(obs.held_features),memory,
        enter_allowed=tensor(enter,torch.bool),exit_allowed=tensor(obs.exit_allowed,torch.bool),
        stop_allowed=tensor(obs.stop_allowed,torch.bool),target_allowed=tensor(obs.target_allowed,torch.bool))


@torch.no_grad()
def collect_session(policy, session, environment, *, device,
                    max_orders_per_second=64, deterministic=False, max_clocks=None,
                    progress_callback=None):
    """Fresh on-policy proposals, 1s decisions, later quote-bound execution.

    Terminal exit is submitted one second before known session end. Unfilled
    remainders stay visible; marked terminal equity is not a fabricated fill.
    `max_clocks` is only for diagnostics, never a production training split.
    """
    from research.rl_trading.v1.common import bounds
    if max_orders_per_second<1:
        raise ValueError('Positive computational order bound required')
    policy.eval()
    state,memory = _initialize(policy,session,device)
    events = iter(session.candle_events())
    event = next(events,None)
    if event is None:
        raise ValueError('No certified candles')
    end = bounds(session.day)[1]
    if max_clocks is not None:
        if max_clocks<2:
            raise ValueError('Canary requires at least two clocks')
        end = min(end,event.close_us+max_clocks*1_000_000)
    frames,steps = [],[]
    previous_equity = environment.account.initial_cash
    previous_penalty = environment.shaping_penalty
    for clock in range(event.close_us,end+1,1_000_000):
        outcomes = environment.advance(clock)
        memory = _remember(policy,state,memory,outcomes)
        if event is not None and event.close_us==clock:
            indices,rows = event.listing_index,event.bank_row
            event = next(events,None)
        else:
            indices,rows = np.empty(0,dtype=np.int64),np.empty(0,dtype=np.int64)
        frame = PolicyFrame(clock,indices,rows,outcomes)
        scalar = _advance(policy,state,session,frame,device)
        valid = scalar[:,SCALAR_NAMES.index('bar_price_valid')]==1
        close = np.exp(scalar[:,SCALAR_NAMES.index('log_close')].astype(np.float64))
        for index,price in zip(indices[valid],close[valid]):
            environment.marks[environment.tickers[int(index)]] = (float(price),clock)
        environment.journal.mark(clock,{t:p for t,(p,_) in environment.marks.items()},
                                  {t:c for t,(_,c) in environment.marks.items()})
        equity = environment.journal.equity_marks[-1]['equity']
        if clock == end and end == bounds(session.day)[1]:
            environment.terminal_cost()  # Never charge a bounded canary cutoff.
        penalty = environment.shaping_penalty
        if steps:
            steps[-1].reward += (equity-previous_equity)/environment.account.initial_cash-(penalty-previous_penalty)
            steps[-1].elapsed += (clock-frames[-1].clock)/1_000_000
        previous_equity = equity
        previous_penalty = penalty
        blocked_indices = ()
        if environment.luld is not None:
            blocked_indices=tuple(environment.by_ticker[t]
                for t in environment.luld.paused_tickers(clock) if t in environment.by_ticker)
        if clock==end:
            if steps:
                steps[-1].terminal = True
            frames.append(frame)
            break
        if clock==end-1_000_000:
            for ticker in tuple(environment.account.positions):
                environment.force_exit(ticker,clock)
        for order_index in range(max_orders_per_second):
            obs = environment.observation(clock)
            step = PolicyStep(obs,tuple(environment.by_ticker[t] for t in environment.pending_entries),0,0.,0.,0.)
            if environment.luld is not None:
                step.blocked_indices = blocked_indices
            dist,value = _distribution(policy,state,memory,step,indices,scalar,device)
            if clock>=end-1_000_000:
                # Known terminal liquidation period is part of the causal mask.
                # No policy proposal is collected while mandatory exits run.
                break
            if deterministic:
                token = int(dist.logits.argmax())
                latent = dist.locations[token]
                kind = dist.parameter_kind(token)
                parameter = latent.sigmoid() if kind==1 else torch.nn.functional.softplus(latent) if kind==2 else latent.new_zeros(())
                likelihood = dist.log_prob(token,latent)
            else:
                token,latent,parameter,likelihood = dist.sample()
            step.token,step.latent,step.old_log_prob,step.old_value = token,float(latent),float(likelihood),float(value)
            step.immediate_outcome = environment.submit(token,float(parameter),clock_us=clock,
                order_index=order_index,holdings=obs.held_index)
            if step.immediate_outcome is not None:
                memory = _remember(policy,state,memory,(step.immediate_outcome,))
            frame.steps.append(step)
            steps.append(step)
            if token==0:
                break
        frames.append(frame)
        if progress_callback:
            progress_callback({'close_us':clock,'policy_steps':len(steps),
                'open_positions':len(environment.account.positions),
                'modeled_net_profit':equity-environment.account.initial_cash,
                'modeled_fees':environment.account.fees})
    state.detach()
    if not steps:
        raise ValueError('No policy proposals in rollout')
    metrics = environment.journal.summary()
    metrics.update(environment.risk_metrics)
    metrics['risk_shaping_penalty'] = environment.shaping_penalty
    metrics['luld_sidecar_enabled'] = environment.luld is not None
    metrics['missing_bracket_extrema_buckets'] = environment.missing_bracket_extrema
    metrics['policy_steps'] = len(steps)
    metrics['order_bound_clock_count'] = sum(len(f.steps)==max_orders_per_second and f.steps[-1].token!=0 for f in frames)
    return frames,steps,metrics


def update_session(policy,optimizer,session,frames,steps,*,device,epochs=4,
                   clocks_per_chunk=32,gamma=.999,trace_decay=.95,clip=.2,
                   entropy_coefficient=.01,target_kl=.02,progress_callback=None):
    """Rebuild from session warm-up per PPO epoch; backprop bounded chunks.

    Gradients accumulate over chunks; weights change only after reconstruction
    finishes. No stale hidden-state shortcut and no shuffled market seconds.
    """
    if epochs<1 or clocks_per_chunk<1 or not steps:
        raise ValueError('Invalid chronological PPO bounds')
    rewards = torch.tensor([s.reward for s in steps],device=device)
    old_values = torch.tensor([s.old_value for s in steps],device=device)
    old = torch.tensor([s.old_log_prob for s in steps],device=device)
    terminals = torch.tensor([s.terminal for s in steps],device=device,dtype=torch.bool)
    elapsed = torch.tensor([s.elapsed for s in steps],device=device)
    advantages,returns = elapsed_gae(rewards,old_values,terminals,elapsed,
        bootstrap=rewards.new_zeros(()),gamma=gamma,trace_decay=trace_decay)
    if advantages.numel()>1:
        advantages = (advantages-advantages.mean())/(advantages.std(unbiased=False)+1e-8)
    metrics = {}
    policy.train()
    for epoch in range(epochs):
        state,memory = _initialize(policy,session,device)
        cursor,kl_sum,loss_sum = 0,0.,0.
        optimizer.zero_grad(set_to_none=True)
        iterator = iter(frames)
        while chunk := tuple(islice(iterator,clocks_per_chunk)):
            likelihoods,values,entropies = [],[],[]
            for frame in chunk:
                memory = _remember(policy,state,memory,frame.outcomes)
                scalar = _advance(policy,state,session,frame,device)
                for step in frame.steps:
                    dist,value = _distribution(policy,state,memory,step,frame.indices,scalar,device)
                    latent = dist.logits.new_tensor(step.latent)
                    likelihoods.append(dist.log_prob(step.token,latent))
                    values.append(value)
                    entropies.append(Categorical(logits=dist.logits).entropy())
                    if step.immediate_outcome is not None:
                        memory = _remember(policy,state,memory,(step.immediate_outcome,))
            if likelihoods:
                likelihoods,values = torch.stack(likelihoods),torch.stack(values)
                stop = cursor+len(likelihoods)
                if epoch==0 and not torch.allclose(likelihoods.detach(),old[cursor:stop],atol=1e-5,rtol=1e-5):
                    raise ValueError('Collection/reconstruction likelihood mismatch')
                loss,_ = clipped_ppo_loss(likelihoods,old[cursor:stop],values,
                                          returns[cursor:stop],advantages[cursor:stop],clip=clip)
                loss = (loss-entropy_coefficient*torch.stack(entropies).mean())*len(likelihoods)/len(steps)
                if not torch.isfinite(loss):
                    raise ValueError('Nonfinite PPO objective')
                loss.backward()
                delta = likelihoods.detach()-old[cursor:stop]
                kl_sum += float(((delta.exp()-1)-delta).sum())
                loss_sum += float(loss.detach())
                cursor = stop
            state.detach()
            memory = memory.detach()
            if progress_callback:
                progress_callback({'update_epoch':epoch+1,'processed_policy_steps':cursor,
                    'total_policy_steps':len(steps),'accumulated_loss':loss_sum,
                    'approximate_kl':kl_sum/cursor if cursor else None})
        if cursor!=len(steps):
            raise ValueError('PPO reconstruction lost policy events')
        kl = kl_sum/len(steps)
        if not math.isfinite(kl):
            raise ValueError('Nonfinite PPO KL')
        if kl>target_kl:
            optimizer.zero_grad(set_to_none=True)
            metrics['kl_stop'] = True
            break
        norm = torch.nn.utils.clip_grad_norm_(policy.parameters(),1.,error_if_nonfinite=True)
        optimizer.step()
        metrics = {'update_epochs':epoch+1,'loss':loss_sum,'approximate_kl':kl,
                   'gradient_norm':float(norm),'kl_stop':False}
    return metrics


@torch.no_grad()
def audit_reconstruction(policy,session,frames,*,device):
    """Real collected execution sequence, no optimizer and no teacher labels."""
    policy.eval()
    state,memory = _initialize(policy,session,device)
    largest = 0.
    count = 0
    for frame in frames:
        memory = _remember(policy,state,memory,frame.outcomes)
        scalar = _advance(policy,state,session,frame,device)
        for step in frame.steps:
            dist,value = _distribution(policy,state,memory,step,frame.indices,scalar,device)
            likelihood = dist.log_prob(step.token,dist.logits.new_tensor(step.latent))
            if not torch.isfinite(likelihood) or not torch.isfinite(value):
                raise ValueError('Nonfinite real model reconstruction')
            error = max(abs(float(likelihood)-step.old_log_prob),abs(float(value)-step.old_value))
            largest = max(largest,error)
            if error > 1e-5*max(1.,abs(step.old_log_prob),abs(step.old_value))+1e-5:
                raise ValueError('Real collection/reconstruction differs')
            count += 1
            if step.immediate_outcome is not None:
                memory = _remember(policy,state,memory,(step.immediate_outcome,))
    if not count:
        raise ValueError('No actual model steps for integration audit')
    return {'status':'passed','reconstructed_policy_steps':count,'maximum_absolute_error':largest,
            'optimizer_updates':0,'teacher_targets_used':False}
