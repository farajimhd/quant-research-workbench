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
- the V17 target-sidecar row count;
- a stable hash of every `(canonical_news_id, ticker, published_at_utc)` row.

The V17 contract is bound to that complete identity hash rather than V16's
feature representation hash. Feature-only V16 changes therefore do not force a
target rebuild, while any row reassignment does.

Only target evidence is stored under:

`D:\market-data\prepared\news_reaction_model\v17\market_response_targets_v3`

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

The builder reuses certified `q_live.news_reaction_labels_v3` rows for:

- the last eligible trade strictly before publication;
- exact phase high, low, terminal return, and extrema timestamps;
- canonical SIP trade-condition eligibility;
- quality and observation evidence.

It reads compact events only for evidence missing from that table: within-window
VWAP and quote-test buy/sell/unknown notional, plus the next-session and
five-session ordered paths. Preparation groups a month's tickers into bounded
64-ticker batches. Each worker advances its batch chronologically, issues one
partition-pruned query per required exchange session, and evaluates windows
when their final session arrives. A six-session rolling cache retains the
complete five-session path while bounding resident event memory. Shared
ticker/session evidence is therefore fetched once per batch rather than once
per ticker or article. Every annual-table branch carries an explicit
`event_date` predicate so ClickHouse prunes monthly partitions. The batch size
is configurable through `--tickers-per-query`; concurrency remains separately
bounded by `--workers`. The builder does not rebuild V16 market state.

All exact-event paths retain SIP `ordinal` and are ordered by
`(sip_timestamp_us, ordinal)`. The causal quote test also performs its ASOF join
on that composite event key, so a quote with the same timestamp but a later
ordinal cannot leak backward into a trade. Every source-label lookup uses the complete
article identity `(canonical_news_id, ticker, published_at_utc)`, requires
`applicable = 1`, and rejects corporate-action crossings. V17 independently
checks all row identities and every populated target array; sampling the first,
middle, and last rows is not considered certification.

The quote-test direction matches the QMD contract: at/above ask is buyer
initiated, at/below bid is seller initiated, then midpoint fallback. `Supply`
means observable seller-dominant execution; it never attributes an actor or
claims that the issuer sold shares.

## Outputs

Per response window:

- direction: neutral, upside, downside;
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

## Exact threshold and class rules

All returns are decimal returns relative to the last eligible trade strictly
before publication. For example, `0.01` means `+1%`.

### Meaningful movement

V17 fits one threshold for each response window from valid 2019-2025 training
rows:

```text
move_magnitude = max(abs(high_return), abs(low_return))
meaningful_threshold[window] =
    max(35th percentile of training move_magnitude, 0.001)
```

The floor `0.001` is a `0.1%` move. A target window also requires at least three
eligible observations. Thresholds are frozen before 2026 is materialized, so
validation outcomes cannot affect class boundaries.

### Direction

For each valid window:

```text
up_move   = max(high_return, 0)
down_move = abs(min(low_return, 0))

meaningful_up   = up_move >= meaningful_threshold
meaningful_down = down_move >= meaningful_threshold
```

The three-class rule is evaluated in this exact order:

1. `neutral` when neither excursion is meaningful;
2. `upside` when the absolute upside excursion is larger;
3. `downside` when the absolute downside excursion is larger.

When the absolute excursions are exactly equal, the terminal-return sign breaks
the tie. If the terminal return is also exactly zero, the direction of the later
extremum breaks the tie. Therefore, a large symmetric response is never called
neutral solely because its high and low excursions have equal magnitude.

Comparable meaningful excursions are not discarded. Their dominant side is the
direction target, while their ordering and resolution remain represented by the
path target and their exact high/low values remain in the raw target evidence.

### Path

Path uses direction, terminal return, high/low excursions, and which extremum
occurred first:

- `no_move`: direction is neutral;
- `reversal`: price crosses both sides meaningfully and terminates on the side
  opposite the first excursion;
- `sustained`: terminal return retains at least `60%` of the dominant
  excursion;
- `spike_fade`: the high occurs first and terminal return retains no more than
  `35%` of the upside excursion;
- `flush_recovery`: the low occurs first and terminal return recovers to within
  `35%` of the downside excursion;
- `volatile_mixed`: none of the preceding rules applies.

### Flow

```text
flow_imbalance = buy_notional_share - sell_notional_share
```

- `demand_dominant`: imbalance is at least `+0.12`;
- `supply_dominant`: imbalance is at most `-0.12`;
- `balanced`: otherwise.

### Persistence

Persistence compares the first non-neutral event-phase direction with the
next-session and five-session directions:

- `no_response`: no valid non-neutral response exists;
- `delayed`: event phases are neutral, but a later window is directional;
- `reversal`: a later direction opposes the event-phase direction;
- `multi_session`: event direction persists through the five-session window;
- `next_session`: it persists through the next-session window only;
- `event_phase_only`: none of the preceding persistence rules applies.

`upside` and `downside` are the directional classes used by persistence.

## Direction-contract version

This three-class direction contract is
`news_market_response_targets_v17_direction3_v2`. Its sidecar uses
`market_response_targets_v3`, and the direction head has three logits. The
version and separate root prevent an incomplete four-class sidecar or
four-logit checkpoint from being resumed as this experiment.

## Commands

V16 preparation must complete first.

```powershell
python -m research.news_reaction_model.v17.run_prepare_targets --execute
python -m research.news_reaction_model.v17.run_profile_sizes
python -m research.news_reaction_model.v17.run_train
python -m research.news_reaction_model.v17.run_evaluate --checkpoint <best_val.pt>
```

`run_prepare_targets` is resumable at month boundaries through durable arrays
and a manifest contract. Progress reports completed ticker batches, tickers,
ClickHouse queries, returned eligible events, elapsed time, and ETA. The
builder assigns a unique query ID to every active ClickHouse request. On the
first `Ctrl+C`, it marks the build cancelled, cancels queued ticker batches,
kills only those registered V17 queries, joins the bounded worker pool, closes
the memory maps, and exits with code 130. The interrupted month is intentionally
recomputed on the next run; all earlier monthly checkpoints remain durable.
The training run writes local metrics, latest and best-validation checkpoints,
a run manifest, a Mermaid model diagram, W&B metrics in the existing
`news-reaction-model-v3` project, and final 2026 evaluation.
