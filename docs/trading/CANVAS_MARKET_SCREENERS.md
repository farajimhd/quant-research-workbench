# Canvas market screeners

## Product contract

The market-screening package contains three related Canvas containers that share one field catalog and one point-in-time clock.

| Container | Job | State authority |
| --- | --- | --- |
| Scanner | Cross-sectional state of the available market universe | Canonical market, reference, news, SEC, facts, and derived-score sources |
| Signal Stream | Newest-first observations that satisfy a deterministic rule or a strategy-owned rule | Reconstructed market events plus durable strategy events |
| Watchlist | Small, named set of securities selected by a user or strategy | Persisted membership; projected market values |

The table rows never own copies of market facts. Every displayed market value is projected at the active Canvas clock. This keeps historical replay, backtests, paper trading, and live trading on the same field semantics.

## Shared field catalog

Columns are described by a stable key, label, group, format, provenance, and explanation. The initial groups are:

- Security
- Market state
- Liquidity
- Share supply
- Fundamentals
- Financial scores
- Financial ratios and growth
- Reported fundamentals
- News and SEC
- Signals
- Signal event
- Technicals
- Custom

Provenance is visible in the column picker and table heading:

- `raw`: directly reported or observed by the source.
- `derived`: deterministic calculation from point-in-time inputs.
- `estimated`: explicitly inferred value whose source does not publish a reliable direct observation.

A missing value remains missing. The UI does not substitute zero for unavailable float, fundamentals, news, SEC, or signal evidence.

## Signal Stream persistence

Deterministic market rules are reconstructed from canonical historical inputs and are not separately persisted merely for the UI. Initial rules include:

- 5% and 10% five-minute price moves in either direction
- continuation when the five-minute and scanner-window returns agree
- trade-arrival and quote-arrival activity bursts
- hot ticker-news and SEC-disclosure events

Strategy-owned, model-generated, discretionary, or otherwise non-deterministic signals must be persisted by the strategy runtime with their detection timestamp, symbol, rule or model version, direction, evidence, and correlation identifiers. Signal Stream merges those durable rows with reconstructable market events without changing either authority.

## Watchlist ownership

Watchlists have a stable name and an owner kind of `user` or `strategy`. Canvas persists membership and presentation settings per container instance. A strategy may update only lists it owns; user lists remain user-controlled. Price, activity, news, SEC, facts, and derived scores are read from the shared scanner projection rather than stored in the watchlist.

## UI behavior

- Columns fit their content and overflow horizontally when the container is narrower than the selected schema.
- There are no vertical row dividers; alignment, whitespace, and semantic typography carry the table structure.
- Unselected sort controls are revealed on header hover or keyboard focus.
- Search, quick filters, views, sorting, and selected columns remain local to the container instance.
- The grouped column picker searches the full catalog and explains every field before selection.
- There is no table-wide technical interval and the **Technicals** catalog
  contains formula definitions, not one copy per timeframe. Only equations
  that require a measurement interval expose **Interval** in their column
  heading popover. That customized interval is persisted with the column.
- Anchored metrics expose their real formula parameters instead. VWAP and Price
  vs VWAP expose **Anchor** (extended or regular session) and **Source** (HLC3
  or exact trades), not a bar timeframe.
  Relative volume shows its extended-session anchor and 20-session baseline.
  Non-windowed fields show no irrelevant interval control.
- Interval definitions use `technical__<metric>__<interval>` keys. Anchored
  definitions use `technical__<metric>__<anchor>`. The customized definition
  appears under **Custom**, where it can be hidden and restored without losing
  its valid parameters.
- Selecting a column heading opens the column tools. Configurable technical
  columns expose only formula-relevant parameters; all non-pinned columns expose explicit
  ascending/descending sort, move left/right/start/end, and remove actions.
  Logo and Symbol remain pinned identity columns.
- Scanner identity is fixed at the left edge of the selected schema. A narrow, unlabeled first column contains only the provider logo when one exists; a missing logo leaves that cell blank. The adjacent Symbol cell contains the ticker followed by compact company-news and SEC recency icons. Missing recent events leave no placeholder ornament.
- Scanner, Signal Stream, and Watchlist body rows use one fixed 42 px logical height before global UI scaling. Logos render at 28 px inside a 38 px identity cell with a 6 px leading inset, while News and SEC recency glyphs render at 15 px; rows without either asset retain the same height and alignment.
- Event icons have no badge background or border: company News uses a filled flame, SEC uses the filing-check mark, hot events use the danger color, and cold events use the information color. Old and absent events render no ticker-cell icon. The News icon is restricted to classified company news, so broad market or editorial coverage cannot mark a ticker.
- Compact News and SEC badge columns sit at the right edge of every default schema so market-state comparisons stay adjacent. They contain explainable classifications (for example, news topics and SEC form classes), not duplicate recency icons. Their exact labels and independent hot/cold states are available in the table filter.
- Selectable rows retain the normal pointer and use a quiet selection tint on hover. Selecting with pointer or keyboard assigns the list container the first unused Canvas link color, creates a dedicated Chart on the same link, and applies the selected symbol. Later selections reuse and focus that exact linked Chart; closing it does not discard the pairing, so the next selection reopens it instead of taking over an unrelated chart. An explicit row-open request exits any conflicting fullscreen surface before raising the Chart, so creation cannot succeed invisibly behind a fullscreen Scanner.
- The column picker exposes source coverage for batch-projected reference fields. It does not advertise fields whose canonical materialized authority is empty or unavailable.
- Positive and negative market values use semantic theme colors; neutral and unavailable states stay visually distinct.

## Source and scale behavior

QMD live scanner state is the live cross-sectional authority. Historical and replay screens use a causally materialized full-universe snapshot from `q_live.canvas_historical_scanner_v1`:

1. The first request for a market clock performs one set-based aggregation over the compact SIP event partitions. It does not fan out into per-ticker requests.
2. The result is stored by snapshot clock, lookback, schema version, and source revision.
3. Later requests reuse the stored rows while the compact-event continuity revision is unchanged.
4. A changed upstream revision causes a new snapshot revision to be written; older rows remain auditable.

The dedicated `GET /api/trading/canvas-scanner` route makes the Scanner, Watchlist, and market-derived Signal Stream independent of the broad Canvas preview request. An unrelated QMD History coverage failure therefore cannot replace a valid persisted scanner snapshot with a six-symbol sample or an empty universe. News and SEC enrichments are attached in batch at the same clock and report their failures separately from market-state availability.

### Technical calculation projection

The scanner's technical fields use a second causal, cross-sectional cache in
`q_live.canvas_scanner_technical_v3`. This belongs to the historical scanner
authority rather than a per-symbol chart request:

1. The frontend sends only the distinct calculation windows required by visible
   custom columns. A calculation window may be an interval bucket or a session
   anchor; those concepts are not conflated.
2. Interval metrics align to the 04:00-20:00 New York extended-session grid and
   never read beyond the Canvas clock. At an exact interval boundary they
   return the just-completed interval; between boundaries they return the
   current causal partial interval.
3. Session VWAP begins at the selected anchor: 04:00 ET for extended session or
   09:30 ET for regular session. The standard default uses canonical one-minute
   HLC3 source bars:

   `VWAP = cumulative(HLC3 × bar volume) / cumulative(bar volume)`

   The popover can instead select exact eligible trade prices:

   `VWAP = sum(trade price × trade size) / sum(trade size)`

   The one-minute HLC3 source resolution is part of the canonical scanner
   calculation, not a user-facing chart timeframe. Price vs VWAP compares the
   latest eligible trade with the same anchored and sourced value.
4. One set-based compact-event query computes each requested calculation window
   for the whole market. No ticker fan-out is allowed.
5. Rows are cached by calculation end, calculation window, schema version, and
   compact-event source revision. Repeated scanner, watchlist, and signal-stream
   requests reuse the same projection.
6. An upstream continuity revision creates a new cache revision rather than
   mutating the prior auditable result.

Available technical metrics are interval price change, volume, dollar volume,
trade count, quote count, high, low, and range; anchored VWAP and price relative
to VWAP; and session-relative volume. Prices and VWAP use eligible compact trade
events; quote count uses consolidated quote events.

Relative volume is explicitly a pace estimate, not a same-clock empirical
average. It is:

`cumulative session volume / (prior 20 completed extended-session average volume × elapsed session / 16 hours)`

It therefore exposes its 20-session baseline and session anchor rather than an
arbitrary bar timeframe. Missing history remains unavailable rather than
becoming zero.

News and SEC enrichment is batch-linked to ticker identity. The scanner uses ticker-aggregated queries over the complete causal news and filing windows rather than reusing the 30-item All News/All SEC preview queries. Company-news classification happens before ticker aggregation; SEC aggregation uses the event-valid CIK-to-market bridge. Identity, issuer, country, market-cap, share-supply, float, and short-interest are resolved causally for the entire tradable universe. The same set-based projection attaches the current canonical logo asset as non-market presentation metadata. Every market and filing source is bounded by the Canvas clock, including filing publication availability and reference-table insertion time. The table never issues per-row fact requests. Field coverage is returned with the snapshot so users can distinguish a partially published source from a broken column.

Financial enrichment follows the same batch contract. One set-based read derives each ticker's CIK from the point-in-time `q_live.feature_tradable_universe_v1` issuer identity—the same authority used by Stock Facts—and joins it to `q_live.sec_xbrl_company_fact_v3`. This remains causal even when `id_sec_market_bridge_v3` is rebuilt after the replay clock; bridge insertion time is therefore not allowed to erase historically available issuer identity. The query deduplicates reported facts by ticker, tag, fiscal period, unit, and availability clock, and retains bounded comparable history. The service then reuses the exact Stock Facts and XBRL functions rather than maintaining scanner-only formulas. The projection exposes:

- XBRL overall quality, evidence coverage, and the profitability, growth, cash-quality, balance-sheet, and capital-discipline facets.
- Stock Facts financial trajectory and its profitability, cash-generation, and balance-sheet subscores.
- Share-base pressure and discipline plus the descriptive historical P/E regime.
- Nineteen aligned derived measures including margins, returns, liquidity, leverage, growth, dilution, and expense intensity.
- Thirty-seven latest reported SEC facts with stable field keys and raw provenance.

The default Fundamentals view contains the most decision-relevant scores and measures. The complete evidence set remains optional in the grouped column picker, which reports actual per-field coverage. Missing XBRL evidence remains unavailable rather than becoming zero, and every filing and recorded timestamp is bounded by the Canvas clock so a historical scanner cannot see a later restatement.

Canvas charts always read the QMD History contract through `GET /api/trading/canvas-chart/history`, including when the selected Canvas clock is close to wall time. The live QMD REST/websocket contract is owned only by the Live Trading workspace. This prevents a historical scanner selection from silently changing data authority based on clock proximity.

Historical scanner rows carry the canonical logo URL returned by their set-based reference projection, avoiding a second ticker-name/path inference query. The bounded presentation endpoint remains a fallback only for strategy-owned symbols that are not in the scanner projection. An unavailable presentation database is returned as retryable service state; it is not cached as proof that the ticker has no logo.

Logo binaries are served from the same `REFERENCE_GATEWAY_PRESENTATION_ASSET_ROOT_WIN` authority used by the reference gateway writer. `REAL_LIVE_LOGO_ARTIFACT_ROOT` remains the explicit serving override; the obsolete trading-dashboard artifact root is accepted only as a legacy final fallback.
