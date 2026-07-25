# News Reaction Model V17

V17 changes the learning task while preserving V16's complete causal input
representation. It predicts observable post-news market-response archetypes
instead of asking one model to forecast ten exact return horizons.

## Non-redundant data boundary

V17 does **not** copy or rebuild V16 embeddings, stock state, time features,
prior same-ticker context, current ticker market state, latest 100 market-news
context items, or market leaders. The V17 loader opens the completed V16 arrays
read-only and validates:

- V16 row count;
- V16 `representation_sha256`;
- the V17 target-sidecar row count;
- a stable hash of `(canonical_news_id, ticker, published_at_utc)`.

Only target evidence is stored under:

`D:\market-data\prepared\news_reaction_model\v17\market_response_targets_v1`

The model calls `NewsReactionModelV16.encode_article()` directly. V16's former
opportunity heads are removed from the V17 module, optimizer, checkpoint, and
forward pass.

## Target windows

Each single-ticker article can have five independently masked response windows:

1. publication to the end of the event-day premarket phase;
2. publication to the end of the event-day regular phase;
3. publication to the end of the event-day after-hours phase;
4. the next complete 04:00-20:00 New York exchange session;
5. the next five complete 04:00-20:00 exchange sessions.

Only the event phase containing publication is applicable. For news published
outside a 04:00-20:00 session, the next exchange session is the first complete
response window.

## Target authority and extraction

The builder reuses `q_live.news_reaction_labels_v2` for:

- the last eligible trade strictly before publication;
- exact phase high, low, terminal return, and extrema timestamps;
- canonical SIP trade-condition eligibility;
- quality and observation evidence.

It reads compact events only for evidence missing from that table: within-window
VWAP and quote-test buy/sell/unknown notional, plus the next-session and
five-session ordered paths. Each worker owns one ticker for a month and caches
every required ticker/session event stream once. It does not issue one query per
article and does not rebuild V16 market state.

The quote-test direction matches the QMD contract: at/above ask is buyer
initiated, at/below bid is seller initiated, then midpoint fallback. `Supply`
means observable seller-dominant execution; it never attributes an actor or
claims that the issuer sold shares.

## Outputs

Per response window:

- direction: neutral, upside, downside, two-sided;
- path: no move, sustained, spike-fade, flush-recovery, reversal,
  volatile/mixed;
- flow: balanced, demand-dominant, supply-dominant.

Across windows:

- no response;
- event-phase only;
- next-session persistence;
- multi-session persistence;
- reversal;
- delayed response.

The meaningful-return threshold for each window is fit only on 2019-2025 target
metrics and frozen before any 2026 labels are materialized. Training uses
2019-2025; evaluation uses 2026.

## Commands

V16 preparation must complete first.

```powershell
python -m research.news_reaction_model.v17.run_prepare_targets --execute
python -m research.news_reaction_model.v17.run_profile_sizes
python -m research.news_reaction_model.v17.run_train
python -m research.news_reaction_model.v17.run_evaluate --checkpoint <best_val.pt>
```

`run_prepare_targets` is resumable at month boundaries through durable arrays
and a manifest contract. The training run writes local metrics, latest and
best-validation checkpoints, a run manifest, a Mermaid model diagram, W&B
metrics in the existing `news-reaction-model-v3` project, and final 2026
evaluation.
