# V7 structure and level-book authority

Read `../../../../doc/05-v7-state.md` and the relevant parts of
`../../../../doc/07-backtest-validation.md` before changing this tree.

- Keep completed-session historical V7 and intraday causal streaming V7 as
  distinct outputs. Only certified historical computation may publish a
  next-session seed. Streaming snapshots are audit/recovery state, not seeds.
- Preserve the existing `arte` historical V7 interval, coverage-fence, and
  terminal builder-checkpoint contract as the starting persisted authority.
  A new Rust historical implementation needs source/condition/split/numerical
  and discrete decision parity before replacing it; no parallel seed store
  may become authoritative by default.
- A seed pins its source generation, predecessor, algorithm/configuration,
  required fit observations, availability, and object hashes. Do not expose a
  retrospective level at a decision before its publication time.
- Use half-open level validity plus a separate knowledge/publication clock.
  Query by both cutoff and pinned generation. An open interval alone does not
  prove past availability.
- A completed one-second candle advances the streaming V7 contract; trades
  update forming market state but do not silently trigger a V7 refit per tick.
  Reject gaps, late input, invalid fit, and corrupt recovery instead of using
  stale or fixed-width substitute levels.
- Preserve observation identity across splits and source corrections. Compare
  representative historical and stream outputs against pinned source evidence,
  including discrete level and strategy-decision parity.
