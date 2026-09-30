# V6 reinforcement learning contract

The existing `training.train_session` is teacher initialization, not RL.
The current attention implementation is `RankedBracketActorCritic`; the
earlier convolution/mean-pooling actor remains an explicit baseline.
`BracketActorCritic` adds a stochastic hybrid actor and a separate value head.
`rl_training.update_on_policy` performs clipped PPO updates from actual policy
rollouts. `run_train` integrates teacher initialization, execution collection,
chronological PPO updates, checkpoint/resume and online W&B replay ledgers.
Production training remains blocked until the complete dataset and real
execution/reconstruction launch audits pass.

## Architecture

Input: 37 scalar channels plus ten V7 slots with 11 channels each (147 total),
per actual completed candle. Per-listing projection and left-causal depthwise
120-candle convolution produce width-128 embeddings. Warm-up loads 120 actual
prior candles. Missing seconds do not advance history. Current-clock market
pooling is linear in listing count. For selected listings, the latest temporal
query attends to the 120 completed projected candles with learned lag
positions and missing-context masking. Eight learned market tokens attend to
these listing representations, then listings attend back to the market
tokens. A lightweight summary of every observed listing enters the tokens.
There is no N-by-N market attention. Execution-outcome GRU carries history.

Ranking uses the sum of V6 `expm1(log_volume)` over the last 15 clock seconds,
not 15 observed bars and not dollar volume. `prepare_training` evaluates
R=500/1000/2000 and chooses the smallest candidate covering at least 99% of
original teacher entries on every training day (an explicit configurable
engineering threshold). Development coverage is reported, not used to choose R.
`sort_secs=1`, eight tokens and four heads are configurable.
Held and pending listings are added to R, so R is neither an absolute compute
cap nor a maximum position count. Ties use the certified identity axis.
Refreshes never rekey action tokens or reset context. The attention query has
no future keys because it receives only the completed ring; no future candle
or teacher score can participate in ranking. Teacher actions outside the
ranked universe are excluded only by the audited preparation step. It removes
the entire entry and recompiles cash, holdings, exits and bracket actions;
filtering loss rows alone would leave an impossible teacher account.

The chronological teacher trainer resets ranking per session, updates it from
the existing bank, tracks pending entry identities until outcomes, and invokes
the attention policy. Quote-aware rollout collectors must likewise call
`reset_market`, `observe_market`, and `set_pending` before decisions; this is
the same ranking and identity history contract is used in the collector.
`benchmark_attention` measures synthetic forward/backward capacity and does
not measure complete rollout throughput or profitability.

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

`rollout.update_session` accumulates bounded chronological chunks and changes
weights only after reconstruction finishes. The 120-candle bank is retained
without copying the full listing history into each gradient graph. Resume is
from a completed session boundary, including pending epoch validation.

Execution reads pinned quote/extrema fields only for submitted or held tickers.
Entries use the first following 100ms IOC bucket; missing quotes are nonfills.
Passive targets require the certified execution-price sidecar; missing evidence
fails closed. Displayed capacity cannot be reused at the same quote timestamp.
This remains a modeled execution scenario, not proof of exchange fills.
The 64-order-per-clock compute bound is recorded when reached and is not a
position-count cap. Price rounding uses canonical 0.0001 research precision,
not an asserted exchange tick. No sealed-test replay occurs in training.

Runnable modules: `prepare_training --help`, `run_train --help`.
`run_train --audit-only` performs no optimizer steps; it still requires the
complete forward audit certificate. No incomplete-data training switch exists.

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
