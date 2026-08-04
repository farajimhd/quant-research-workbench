# BarGPT v1 causal multiscale context contract

## Purpose

This document records the requested BarGPT v1 input contract and the loader
architecture that should implement it. It is a design and implementation
reference; it does not claim that the current loader already satisfies every
requirement below.

## Problem

BarGPT v1 is anchored at a one-second market-bar origin, but the current
loader has one shared `context_bars_1s` setting and derives coarse intraday
row counts from that value. Consequently, the multiresolution views mostly
cover the same physical lookback at different resolutions. That defeats the
purpose of adding coarser views: a 1-hour view should provide substantially
longer history than the 1-second view without requiring the model to rebuild
that history from the 1-second pathway.

The current calendar path also removes the final weekly/monthly group
unconditionally. This avoids future leakage, but it discards valid as-of
context: during a Wednesday origin, the current week should contain Monday
and Tuesday daily bars; during a month, the current month should contain all
daily bars available through yesterday.

The current session/block construction also materializes a bounded block of
origins and relies on the decoder causal mask for earlier origins. That can be
causal, but it is not the desired explicit streaming contract in which every
origin advances one second through pre-built, bounded multiscale state.

## Fixed model views and default context parameters

The view names and ordering are fixed model architecture parameters. Context
lengths are data/training hyperparameters and may be changed for experiments.

### Intraday views

Every intraday view is calculated from the one-second authority. The default
context length is 720 completed bars per view:

| View | Context bars | Physical lookback |
|---|---:|---:|
| 1s | 720 | 12 minutes |
| 5s | 720 | 1 hour |
| 10s | 720 | 2 hours |
| 30s | 720 | 6 hours |
| 1m | 720 | 12 hours |
| 5m | 720 | 2.5 days |
| 30m | 720 | 15 days |
| 1h | 720 | 30 days |

The loader calculates the required one-second warmup dynamically:

```text
max(context_bars[view] * timeframe_duration[view])
```

With the defaults above, the 1-hour view determines the maximum intraday
lookback: 720 one-hour bars, or 30 days of the configured one-second market
clock. The exact number of raw rows is derived from the session clock and
availability, not assumed to be a continuous 24-hour clock.

### Calendar views

Calendar context is based on daily session bars available through yesterday of
the current one-second origin:

| View | Context bars | Construction |
|---|---:|---|
| 1D | 90 | Last 90 daily bars through yesterday |
| 1W | 52 | Weekly as-of bars formed from daily bars through yesterday |
| 1MO | 12 | Monthly as-of bars formed from daily bars through yesterday |

The current partial week and month are valid as-of bars. For a Wednesday
origin, the current weekly bar contains Monday and Tuesday; it must not contain
Wednesday's unfinished daily bar. The monthly bar follows the same rule.
To construct 12 monthly bars safely, the warmup loads approximately 365 prior
daily session bars, or the dynamically calculated equivalent for a different
calendar policy.

## Required causal semantics

Every emitted example has a one-second origin timestamp `t`.

For every input view, every selected bar must satisfy:

```text
available_at_us <= t
```

Intraday bars are completed aggregates of one-second rows up to the current
origin. A partially completed 5-second, 1-minute, or 1-hour bucket is not
included as a completed context bar. The current one-second row may be
represented according to the established bar-availability convention, but no
future one-second row may contribute to any view.

Daily, weekly, and monthly context is frozen at the latest daily bar available
through yesterday. Daily rows after that cutoff cannot contribute to calendar
aggregation for the origin.

Future support used for target construction remains separate from context
state. It may be loaded ahead for throughput, but it must never enter a
context ring before its availability time.

## Desired loading and training process

### Ticker-month initialization

For each ticker-month work unit:

1. Resolve the first one-second training origin.
2. Calculate all intraday context lengths and the maximum physical warmup.
3. Stream the required prior one-second range through the intraday rollup
   state, including 1s, 5s, 10s, 30s, 1m, 5m, 30m, and 1h.
4. Load the required daily history, normally approximately 365 daily session
   bars, and initialize daily, weekly, and monthly as-of state.
5. Retain only bounded completed-bar rings and current in-progress aggregation
   buckets. Warmup raw rows are discarded after they have been consumed.

### Origin streaming

For each successive one-second origin:

1. Consume the next one-second row or bounded Arrow batch.
2. Update all intraday aggregation buckets using vectorized operations.
3. Emit a coarser bar only when its interval is complete at the current origin.
4. Append completed bars to each view's bounded ring buffer.
5. Snapshot exactly the configured number of bars for every view.
6. Assert every snapshot bar is available at or before the origin.
7. Emit the training example and separately attach future target support.
8. Evict rows older than the largest retained context and release consumed raw
   batches.

The implementation should process origins in bounded chunks for GPU efficiency,
but chunking must not change the per-origin causal state. A chunk may share
rollup work; it must not use the last origin in the chunk as the as-of boundary
for earlier origins.

## Efficiency requirements

The loader should not reload or reaggregate the same warmup history for every
origin. Warmup occurs once per ticker-month. Intraday aggregation then proceeds
incrementally from the one-second stream.

The long raw warmup should be streamed in Arrow/Polars batches and discarded
after aggregation. The resident cache should contain only:

- one bounded ring per intraday view;
- one bounded ring for daily bars;
- weekly/monthly as-of aggregation state;
- current incomplete intraday buckets;
- a bounded future target-support buffer.

Vectorized bucket assignment and reductions should be used for batch warmup and
for new rows. Python per-origin/per-row aggregation is not acceptable on the
training path.

## Implementation boundaries

The following components need coordinated changes:

- `research/bar_gpt/v1/config.py`: fixed timeframe set and per-view context
  parameters;
- `research/bar_gpt/v1/data.py`: view contracts, bounded snapshots, and
  availability assertions;
- `research/bar_gpt/v1/loader.py`: dynamic warmup, streaming rollups, daily
  as-of calendar aggregation, and bounded eviction;
- `research/bar_gpt/v1/model.py`: fixed view ordering and input/output artifact
  contract for the added 10s and 30m views;
- `research/bar_gpt/v1/train.py`: loader construction, resolved-contract
  evidence, and checkpoint contract hash;
- focused tests: origin causality, calendar cutoff, warmup coverage, rollup
  correctness, bounded memory, and restart/resume behavior.

This design requires a fresh checkpoint contract. Existing checkpoints trained
with the former view set or context semantics must not be resumed silently.
