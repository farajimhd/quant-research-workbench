# Bars, indicators, quotes and execution data

[Overview](../certified_research_dataset_design.md) · [Previous](03-ingestion-universe.md) · [Next](05-v7-checkpoints.md)

Find each market-data form, its timing and its downstream purpose.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Shared bars and activity](#shared-bars-and-activity)
- [Technical indicators and market histories](#technical-indicators-and-market-histories)
- [Quotes and execution evidence](#quotes-and-execution-evidence)
- [Existing derived features and optional extensions](#existing-derived-features-and-optional-extensions)

## Shared bars and activity

### Frames, keys and fields

Authority/form: versioned ARTE-style sparse bars keyed by build/attempt, session, listing and resolution. Current market-day frames are 100 ms, 1 s, 5 s, 10 s, 30 s, 1 min, 5 min and 1 hour. Include OHLC, notional, volume, trade count, execution-specific volume/notional and validity. Publish session/regular daily summaries needed by float, prices and prior-close consumers with explicit source bindings.

### Sparse observations and adjustment basis

Preserve price-valid, extrema-valid and activity-only rows. No-trade clocks do not become synthetic price candles. Cumulative session VWAP and rolling activity reuse the same certified aggregates. Daily summaries declare PM/regular/AH scope and adjustment basis. Split-adjusted model views retain original unadjusted data and adjustment evidence.

## Technical indicators and market histories

### Indicators and continuity

Current SQL catalog: EMA 7/9/12/15/20/26/50, MACD 12/26/9 including histogram, RSI14 and ATR14 at supported frames, with readiness flags. EMA/MACD carry certified predecessor state; existing RSI/ATR session reset semantics remain versioned. A request for alternative periods/resolutions produces a new declared product, not an execution-time substitution.

### Rolling activity and RVOL

Forms: sparse indicator rows aligned to eligible bars; scalar session-boundary state; rolling-activity/RVOL summaries; optimizer history banks. Current RL uses same-clock prior-session 10-second RVOL, not an assumed 20-session default. Additional RVOL windows are launch parameters. Cumulative/session extrema, VWAP and rank state must have declared reset/carry semantics.

### The 332 optimizer history channels

Optimizer's 332 history channels comprise trailing recent highs (1–60), lagged closes (1–60), attention/notional means (1–60), average moves (2–32), and 121 confirmed swing-low combinations from windows 1/2/3/4/5/10/15/20/30/45/60. Preserve elapsed-second gaps and confirmation clocks. Do not substitute observed-candle windows.

## Quotes and execution evidence

### Quote and execution forms

Forms: certified 100 ms liquidity rows; sparse quote updates with timestamp, bid/ask and size; execution-price/price-level capacity evidence; next-observed-trade lookup; terminal marks. Quote snapshots retain age, freshness and invalidity. V7 optimization currently uses latest causal second-close snapshots, with quotes older than one second invalid; this is not bid/ask OHLC.

### Execution evidence versus fill assumptions

Detailed RL/optimization V6 execution needs fresh arrival quotes, extrema and passive-target price-level volume. Displayed size and price-level volume are modeled optimistic bounds, not queue-position evidence. Reuse canonical aggregation/intermediate projections where sufficient. If exact price-level evidence requires another canonical pass, materialize it once and disclose that measured exception to the single-read objective.

### Offline execution tape

Prepare a reusable indexed execution tape for the selected range; current RL environment reads submitted/held-ticker evidence lazily. Add an offline adapter so repeated PPO runs can use certified indexed shards without per-step database round trips. This adapter preserves arrival/freshness/partial-fill semantics; it does not replace execution evidence with 1-second candle volume.

### Tick size, LULD and halt evidence

Historical tick-size rules and instrument/LULD tiers require dated, versioned evidence. Current `v6/luld.py` explicitly implements modeled research bands/pauses, not official SIP halt/band evidence. Record the modeled scenario contract separately; require official dated halt/LULD sources if a use case requests authoritative execution restrictions. Do not infer historical tick size from observed price decimals.

## Existing derived features and optional extensions

### Existing downstream derived products

Catalog existing candidate/admission, HOD, pivot, executable-price and strategy-facing V7 projection products discussed in this chat. Where current consumers require them, regenerate them from the shared strict-policy source and certify their dependencies; do not assume core bars imply coverage of these products. Preserve generic market features separately from strategy-specific admission policies. A narrow candidate funnel cannot remove symbols needed by a broader RL/optimization rule search.

### Ranking and consumer-specific timing

Ranking views include the declared price filter, rolling dollar/share volume window, tie-breaking and eligibility clocks. They are consumer-configured projections of the full certified population. Prepare cumulative extrema/VWAP and rank context once for identical contracts; different windows/policies get distinct hashes. For RL strict-prior target t, joins stop before t; optimizer second-close observations can include completed information through that close. One adapter cannot silently apply the other timing convention.

### Explicit optional extensions

News/SEC narrative synthesis, embeddings, borrow inventory/rates and book-depth data are not current mandatory inputs demonstrated by these consumers. Record them as explicitly optional catalog extensions with independent authorities, availability and coverage. Existing UI narratives or current-only snapshots must not be promoted to certified training features. Encoder future-return pretraining, if selected later, gets separate masked target shards; those targets are not the PPO critic or causal observations.

[Overview](../certified_research_dataset_design.md) · [Previous](03-ingestion-universe.md) · [Next](05-v7-checkpoints.md)
