# Structural V7 and session checkpoints

[Overview](../certified_research_dataset_design.md) · [Previous](04-market-data.md) · [Next](06-fundamentals-short-data.md)

Understand causal state, retrospective labels and accelerated checkpoint backfill.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Structural Level Book V7](#structural-level-book-v7)
- [Example: starting an AH replay](#example-starting-an-ah-replay)
- [State versus displayed levels](#state-versus-displayed-levels)

## Structural Level Book V7

### Prepared outputs

Forms: normalized causal level/observation updates; lookup projections; full resumable checkpoints at PM close, regular close and AH close; coverage/predecessor receipts. Retrospective full-day labels are a separate, clearly named output.

### Why retrospective labels cannot seed intraday state

The historical Level Book campaign uses whole-day range/noise in fitting parameters. It cannot produce causal intraday state simply by saving its intermediate values. Use the causal streaming kernel and prefix-only parameter selection for the new checkpoint family. Preserve existing pre-04:05 trade exclusion unless a separate approved structural policy changes it.

### Session boundaries and checkpoint contents

Standard boundaries are 09:30, calendar regular close (normally 16:00), and supported AH close (normally 20:00 ET). Use half-open input ranges and completed bucket availability. Checkpoints retain pending reactions, observations, fits, roles, armed state, counters, source cursor, parameters, predecessor hash and split evidence. Day rollover/split adjustment occurs once, not at every session restoration. Empty segments carry valid state with explicit coverage.

### New generation and accelerated backfill

New history: prior AH → PM → checkpoint PM → regular → checkpoint regular → AH → checkpoint AH. Backfill: compatible prior AH → shared PM/regular bars → same kernel → missing PM/regular checkpoints. Preserve existing AH unless equivalence proves reuse; old retrospective/legacy state is not an acceptable causal shortcut. Repair/rebuild prerequisite state first when incompatible. Consumer constructors currently requiring a prior-date seed need explicit same-day full-state restore support.

### What else AH initialization needs

Regular-close V7 state alone does not initialize AH research: indicator, VWAP/extrema, rolling activity/rank and relevant market state need compatible session-start sidecars too. Account/positions/order state belongs to a scenario-specific replay checkpoint, not universal market data.

## Example: starting an AH replay

For a normal session, load the certified regular-close checkpoint and begin with bars available after 16:00 ET. That avoids reprocessing PM and regular hours. The checkpoint must contain the full causal engine state, and compatible indicator/rolling-market state must be available too. If the old daily checkpoint includes retrospective full-day information, it cannot supply that shortcut.

## State versus displayed levels

Displayed levels are the current visible result. Full state also includes unfinished reactions and observations that can affect the next bar. Saving only visible levels cannot guarantee that a restored engine behaves like uninterrupted processing.

[Overview](../certified_research_dataset_design.md) · [Previous](04-market-data.md) · [Next](06-fundamentals-short-data.md)
