# Historical Backtest and Debug runtime

Read `../../../../doc/07-backtest-validation.md` and
`../../../../doc/08-performance-operations.md` before changing this tree.

- Backtest is a separate process and permission domain. It must not possess
  live broker credentials, submit live orders, mutate live namespaces, or
  consume Live's reserved CPU, RAM, or ClickHouse capacity.
- Plan the smallest required universe and columns. Use certified ClickHouse
  products and completed 100 ms bars as the initial execution grid. Vectorize
  scanner, Watchlist, Signal Stream, indicator, and rule preparation across
  bounded columnar batches and independent tickers.
- Advance V7, strategy state, account reservations, fills, and OMS effects in
  causal order. A bar cannot reconstruct an intrabar trade/quote crossing;
  use bounded exact event refinement or an explicitly labeled approximation.
  Never claim event-level parity from bar-only evidence.
- Use the same strategy, portfolio, risk, and order contracts as Live. Inject
  historical clock, source, and simulated execution; do not fork rule logic.
  A missing historical receive timestamp remains missing.
- Pin source generation, configuration, execution interval, cost/fill model,
  journal scope, and causal cutoff. Persist compact run evidence and logs only
  in ARTE ClickHouse; SQLite is forbidden, including as a temporary spool.
- Journal decisions and fills before advancing authoritative projections.
  A verified fill receipt must be applied to account-owned strategy state with
  its causal entry evidence; position mismatch blocks subsequent decisions.
- Saved Review is read-only. Pause/step/resume acts on an isolated run only
  after explicit state validation. Checkpoint recovery must equal uninterrupted
  replay under the same pinned inputs.
