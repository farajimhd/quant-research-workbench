# Shared strategy chart presentation contract

The chart renderer, not a strategy, owns visual grammar. A strategy publishes
typed, timestamped decisions, protection changes, orders, and executions through
the normal trading journal. The chart adapter reads those verified facts and
maps them to the shared `strategyPresentationContract.ts` action vocabulary.
The same mapping is used for live and saved Backtest; strategy source code must
not supply CSS, colors, label prose, marker geometry, or presentation settings.

## Evidence and labels

- A decision marker means an instruction was issued. A fill marker means an
  execution occurred. A requested stop/target change is distinct from an
  effective broker protection change. Do not render one as another.
- The marker and rail already convey entry/exit direction. Labels contain only
  useful additional evidence: direction where needed, quantity, price, P&L,
  and a **verified** cause such as `Stop hit` or `Target hit`.
- Do not put `issued`, `filled`, `exit`, or `@` into ordinary labels merely to
  repeat the marker or price context. Preserve `Partial` and filled/requested
  quantity when that distinction matters.
- If a reason is absent, leave the reason label absent. Never infer stop/target
  from a favorable price, P&L, proximity to a line, or an order request.
- Segment text by semantic role (`reason`, `size`, `priceLong`, `priceShort`,
  `exitPriceLong`, `exitPriceShort`, `pnlWin`, `pnlLoss`). The existing shared
  Strategy Presentation settings own visibility, font, fill, edge, and color
  for these roles across all strategies.
- Stop and target moves use short `SL` and `TP` labels, with their numeric price
  in a separate segment. Keep exact event times and price coordinates; do not
  spread an event over a candle or fabricate a bar-resolution event order.
- Saved Backtest rails come from committed, hash-verified, broker-effective
  `trading_protection_change_v3` facts joined to the lifecycle through opening
  order IDs. Requested changes never become rails. A `Stop hit` or `Target hit`
  label is allowed only when the closing fill references the exact effective
  protection order; an unlabeled exit marker is preferable to a guessed cause.

## Adapter rule for a new strategy

Add a semantic reason code to the shared reason vocabulary if necessary, with
tests and a source-evidence mapping. Do not add a strategy-specific label
renderer or fork the configuration UI. Provide the same typed journal fields in
Backtest and live paths. If one path lacks the evidence, show an unavailable
state or an unlabeled marker; do not borrow the other path's conclusion.

The chart is a read-only projection. Presentation preferences are local to the
user and never modify strategy decisions, journal rows, fills, or P&L.
