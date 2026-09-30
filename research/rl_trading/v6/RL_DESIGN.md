# V6 reinforcement learning contract

The existing `training.train_session` is teacher initialization, not RL.
`BracketActorCritic` adds a stochastic hybrid actor and a separate value head.
`rl_training.update_on_policy` performs clipped PPO updates from actual policy
rollouts. These components do not yet constitute a production OMS collector
or an audited full-campaign launcher. Production training remains blocked.

## Architecture

Input: 37 scalar channels plus ten V7 slots with 11 channels each (147 total),
per actual completed candle. Per-listing projection and left-causal depthwise
120-candle convolution produce width-128 embeddings. Warm-up loads 120 actual
prior candles. Missing seconds do not advance history. Current-clock market
pooling is linear in listing count; there is no temporal attention and no
quadratic all-market attention. Execution-outcome GRU carries action history.

Masked action tokens are HOLD, ENTER_LONG(listing), EXIT_LONG(holding),
SET_STOP(holding), SET_TARGET(holding). ENTER samples a sigmoid-normal cash
fraction; stop/target sample softplus-normal positive log-price distance.
Store both token and latent sample. The joint likelihood includes only the
selected continuous branch and its transform Jacobian. Tick rounding and
partial fills belong to the environment and cannot replace the sampled
proposal in likelihood calculations. Existing deterministic policy and
teacher-loss APIs remain available for initialization/baseline comparison.

The critic consumes detached current market embeddings, account state and
action memory. Teacher terminal profit and oracle extrema never enter its
inputs or return targets. This conservative separation prevents critic
warm-up from modifying the actor. A richer separately trained critic encoder
is a subsequent measured architecture comparison, not an assumed improvement.

## Training and audit requirements

1. Complete hash/identity/source/schema audits for all 16 training and two
   development dates. July30 is context only. August26 remains sealed.
2. Initialize actor with candle-only teacher actions/size/bracket labels.
   Quotes must not alter teacher labels. Future extrema are target tensors
   only; never observation, eligibility mask or initial recurrent state.
3. Collect fresh on-policy trajectories in the quote-aware OMS. Use causal
   completed candles and confirmed outcomes; entries compound model cash
   subject to existing share caps. Reward is change in marked net equity
   divided by initial equity, including costs exactly once. Report quote
   gaps, partial fills and ambiguous bracket hits; no verified-fill claim.
4. Reconstruct recurrent states chronologically with current parameters for
   each PPO update. Never shuffle individual events or reuse stale hidden
   states. The first reconstructed likelihood must match collection.
   Detach bounded-BPTT boundaries and reconstruct prior burn-in context.
5. Compute GAE from actual rollout returns. Discount uses elapsed seconds,
   including zero elapsed time for multiple orders at one clock. Terminal
   clears continuation; truncation bootstraps the actual next causal state.
6. Select on development closed-loop net/fees/drawdown/turnover/holding and
   exit behavior; report representative train replay as in-sample. Only then
   perform one sealed heldout replay. No launch on incomplete audits.

The implemented update core takes a chronological reconstruction callback;
it does not certify an arbitrary callback or supply missing OMS integration.
Full market collector, audit certificate binding and runnable production
launcher must be integrated and validated before declaring RL training ready.

## Research basis and limits

PPO alternates environment collection and policy optimization:
https://arxiv.org/abs/1707.06347

Hybrid actor-critic supports discrete actions with conditional continuous
parameters, directly matching entry sizing and bracket distances:
https://www.ijcai.org/proceedings/2019/316

Demonstration initialization followed by environment learning is supported by
demonstration-augmented policy-gradient research:
https://arxiv.org/abs/1709.10087

A pure return-conditioned Decision Transformer is a sequence-modeling
alternative, not this requested on-policy RL training contract:
https://arxiv.org/abs/2106.01345

This is an engineering choice for bounded full-market compute, not a claim
that one architecture is universally best. No profitability is established
by synthetic optimizer or causality tests.
