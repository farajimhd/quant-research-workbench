# RL trading inputs and required upgrades

[Overview](../certified_research_dataset_design.md) · [Previous](06-fundamentals-short-data.md) · [Next](08-optimization-inputs.md)

Separate causal market observations, hindsight supervision and policy rollouts.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [RL V6 consumer forms and upgrades](#rl-v6-consumer-forms-and-upgrades)
- [Why inputs and labels are separate](#why-inputs-and-labels-are-separate)

## RL V6 consumer forms and upgrades

### Current contract authority

Current authority is `LABEL_PREPARATION.md` and `TEACHER_SEQUENCE.md`, which supersede legacy numbered portfolio preparation. Current dataset/algorithm versions must be pinned from those contracts at launch, not inferred from a README's older examples.

### Prepared banks, targets and rollouts

- Sparse identity-packed bank: int64 close clocks, `[actual_rows,37]` float32 scalars, `[actual_rows,2,5,11]` V7 slots, identity offsets, masks and hashes. Store each actual row once; loaders form 120-actual-candle histories with prior tails. Preserve activity-only invalid-price rows.
- 37 scalars currently cover OHLC, bar/session VWAP, activity and 60-second sums, prior-session RVOL, MACD/RSI/ATR/EMA, clock/session/gap, float/outstanding/split ages and validity. Ten V7 slots contain relative geometry, counts, confirmation age, role/origin and presence.
- Separate immutable 1a opportunity and 1b selected/grouped/suppressed label shards, allocation/forecast/horizon targets and audit evidence. Strict-prior observations exclude the target candle (`close_us < t`). Future gains, grouping/allocation denominators and outcomes remain target-only.
- Existing episode admission uses prior 60-second evidence: ≥20 trades, ≥2,000 shares, ≥10 active seconds and prior-trade age ≤5 seconds. Later >5-second inactivity invalidates hindsight supervision, not causal observations. Thresholds use exact certified activity, not float32 bank fields.
- PPO trajectories are policy/scenario-specific: account and holding observations, sampled proposals, actual modeled execution, rewards, log probabilities, termination/truncation, elapsed time and next state. They cannot be universal precomputed labels. The PPO critic needs returns from its continuation policy, not the teacher's future price gain.

### Required adapter upgrades

Required upgrades: strict-unknown build gate; dated universe adapter instead of current static canonical scope; certified as-of reference adapter instead of raw ingestion-filtered float reads; compatible causal V7/session restore; indexed offline execution evidence; tick/LULD scenario evidence; new bank/label publication hashes after repaired bars. Retain current missing-V7 masking only where its certificate explicitly permits genuine absence; malformed/incompatible state must not masquerade as absence. A preset requiring levels must certify their presence or proven empty state.

### Adding new reference features safely

Broader fundamentals and short-pressure values are prepared as a sidecar now. Do not silently expand the existing 37-scalar model or reuse checkpoints under a different shape. Their adoption requires a versioned feature/model adapter and training-only normalization, with age, precision and confidence channels.

## Why inputs and labels are separate

At target close t, current RL observations use candles strictly before t. A teacher label may use prices after t to describe what would have been a good action; that future evidence belongs only in the label. PPO rewards then come from the particular policy and its modeled fills, so one universal market dataset cannot precompute every policy's rollout.

[Overview](../certified_research_dataset_design.md) · [Previous](06-fundamentals-short-data.md) · [Next](08-optimization-inputs.md)
