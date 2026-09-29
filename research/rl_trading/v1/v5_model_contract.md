# V5 dynamic order model contract

`model_v5.py` is a new model path. It does not read V4 fixed-lot action shards or
warm-start an R5 checkpoint. Its teacher is the separately certified dynamic
Phase 3 position ledger exported by `dynamic_supervision.py` V2.

At each completed second the causal feature bank contributes one feature vector
per stable listing identity. The temporal encoder projects that second once and
retains the last 120 projected seconds. `encode_chunk` processes chronological
training blocks without rebuilding overlapping windows; serving uses the
equivalent one-second `advance` path. Training passes the cache and action
state forward, detaching at bounded chunk boundaries. A service restart must restore the
identity-keyed temporal state, or warm it with the prior 120 certified causal
seconds. Reset both temporal and action state at the session boundary. Neither
Phase 2 scores nor episode IDs enter observations.

The order decoder receives the current market summary, causally updated
account state (including time since the last executed order), padded active
holdings, and action memory. It emits STOP, BUY of
one candidate listing, or SELL of one active holding. BUY also emits a fraction
of *remaining* available cash. Teacher sales come before purchases within a
second. `allocation_weight` in the ledger is for pre-buy cash auditing;
`remaining_cash_weight` is the autoregressive size target. Teacher-forced
account snapshots must be updated after each order. The sparse order sequence
contains one STOP and padding is masked. No four-lot portfolio cap is embedded
in the network; the padded holding axis and action-step count are batch shapes,
with observed maxima certified from the selected training split.
The holding slots, validity mask, and features must also be refreshed after
each teacher order. In particular, a lot sold at the start of a second cannot
remain a valid SELL target for the next order in that second. The trainer can
encode chronological feature chunks once and decode only seconds with teacher
orders, plus explicit STOP targets for sampled empty seconds.
`v5_order_adapter.py` reconstructs those within-second teacher states from the
certified order and position ledgers, checks cash/profit-bank/realized P&L and
open-lot counts against the trajectory, and maps BUY/SELL tokens to the bound
listing axis and current holding slots. It supplies only causal account and
marked-price features to the policy; the teacher's episode IDs remain adapter
keys and never become input features. Holding age is log-scaled over the full
session, retaining distinctions among one-hour and multi-hour positions.
The same adapter supplies deterministic STOP examples from empty seconds:
all seconds within 15 seconds of a teacher order plus one background second
per minute. Those sampling parameters and the STOP/action balance must be
logged with each training run. Full-session closed-loop replay, rather than
sampled training accuracy, determines whether the policy trades at sensible
times.
STOP does not mutate action memory; empty seconds can be encoded in a block
without invoking the order decoder for every second.

`v5_feature_binding.py` verifies that a feature bank from a prior certified
shard can be reused only when its
listing identities, day, feature contract, causal source, and file certificate
match the new teacher's V7 population. Its old action, reward, and account
arrays are not V5 labels. The binder proves that every teacher action's ticker
is represented on the stable listing axis. The chronological training adapter
must also map each active holding to that axis, construct the within-second
account snapshots and masks, and fail closed on omissions. No label-dependent
candidate addition is permitted.

The teacher allocates against a fixed initial bankroll and segregates realized
gains. Model replay instead carries profit forward and applies the separately
specified equity/price sizing limits. Replay must log ordered positions, fees,
holding time, turnover, unfilled/partial decisions and period P&L, with
training-session diagnostics, development selection and one sealed test replay
after selection. Close-price hindsight profits alone do not certify executable
fills. Do not train until the complete train/development supervision inventory
and teacher audit reconcile.
