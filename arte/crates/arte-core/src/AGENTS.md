# Shared domain, strategies, and computational contracts

Applies to the flat modules in this directory and their child directories.
Read `../../../doc/02-architecture.md`, `../../../doc/06-trading-broker.md`,
and `../../../doc/07-backtest-validation.md` for the relevant contract.
For `market_structure.rs`, also follow the V7 rules in
`market_structure/AGENTS.md`; that child file does not apply by path to this
flat parent module.

- Keep this crate deterministic and independent of HTTP, ClickHouse, brokers,
  UI, clocks read from the host, and the temporary parent repository. Inject
  source, clock, and execution evidence through typed inputs.
- Use one domain implementation for Live and Backtest. Historical mode may
  lack receive or execution timestamps; preserve that absence. Never let
  retrospective evidence satisfy a live-only condition.
- Pin each strategy's exact code and effective configuration. Strategy 350 is
  a replacement candidate, not approved live authority. Do not enable entries
  from a partial port or substitute an older candidate for missing rules.
- Every strategy, Watchlist, Signal Stream, scanner rule, indicator, level book,
  rule set, and named computation declares its own execution interval. It is
  `events` or an approved fixed 100 ms multiple, independent of input bars.
  Include it in identity, scheduling, journals, and replay tests.
- Preserve causal order within an instrument and deterministic account order
  across instruments. Vectorize independent columnar work, but do not batch a
  live event past its decision deadline or use future bars/levels.
- Keep market-derived features shared; key position, fill, risk, and lifecycle
  state by run, mode, strategy, account, and instrument. Stage state changes
  until the matching journal readback succeeds. Failed evidence must not
  mutate committed state.
- Strategy emits intents. Portfolio sizes/reserves, risk validates, and OMS
  executes. No strategy module may submit directly to a broker or assume that
  a proposed bracket is broker-held protection.
- Test prefix invariance, missing operands, duplicate/reordered observations,
  checkpoint restore, and Live/Backtest decision parity for changed rules.
