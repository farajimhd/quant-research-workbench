# Canvas market screeners

## Product contract

The market-screening package contains three related Canvas containers that share one field catalog and one point-in-time clock.

| Container | Job | State authority |
| --- | --- | --- |
| Scanner | Cross-sectional state of the available market universe | Canonical market, reference, news, SEC, facts, and derived-score sources |
| Watch Universe | Versioned eligible ticker membership used by one or more Run Plans | Materialized Market Discovery configuration and its registered universe resolver |
| Signal Stream | Newest-first immutable rule occurrences from the current exchange session | QMD Live hot cache plus durable QMD occurrence table; QMD History run materialization for Replay, Backtest, and Debug |
| Strategy Activity | Newest-first signals, decisions, and campaign changes for Strategy Runs | Durable trading journal records owned by the strategy runtime |

The table rows never own copies of market facts. Every displayed market value is projected at the active Canvas clock. This keeps historical replay, backtests, paper trading, and live trading on the same field semantics.

Opening a Canvas container is never a computation trigger. Market Discovery
publishes the materialized Data Field -> Rule Set -> Signal Stream graph to
QMD. QMD Live continuously evaluates it, persists each false-to-true
occurrence, and serves the current 04:00-20:00 ET session from its in-memory
snapshot. Canvas initially reads that complete snapshot and thereafter requests
only newer sequence values. Replay, Backtest, and Debug use the corresponding
QMD History materialization, filtered by run identity and virtual clock, so
occurrences from different runs cannot mix.

### Signal Stream restart and gap recovery

QMD owns recovery; neither the backend nor Canvas recomputes a Signal Stream
when it is opened. For every enabled Market Discovery stream, the backend
publishes one recovery contract with the materialized configuration revision:

1. QMD hydrates the current session occurrence cache from its durable table.
2. Source-native streams (for example, market halts or news events) retain the
   durable recovery contract of their owning source.
3. Rule-evaluated Core Scan streams compile to the same causal Data Field and
   Rule Set graph in QMD History, bounded from 04:00 ET to a fixed handoff
   cursor just behind live time.
4. QMD History replays the globally ordered market events and emits compact
   false-to-true transitions plus the terminal match state. Historical storage
   is read in bounded chronological windows while one reducer state is carried
   across them; it does not issue one unbounded session query or return full
   cross-sectional candidate snapshots for every evaluation tick.
5. QMD Live continues consuming live data while that materialization runs. It
   retains first-observed baselines until the historical terminal state is
   known, then performs one deterministic handoff.
6. Recovered and live occurrences use the same definition revision and event
   identity, are deduplicated before persistence, and receive one monotonic
   session sequence for snapshot-then-delta delivery.
7. Recovery is marked complete only when QMD History reports both
   `request_complete=true` and `complete_for_history=true`. Missing coverage or
   an unsupported Data Field/aggregation remains explicitly incomplete and is
   retried; it is never reported as a complete empty signal history.

The resulting session list is therefore warm from QMD's in-memory cache,
append-only in the durable occurrence table, and incrementally delivered with
`after_sequence`. Configuration changes create a new content-bound recovery
revision; page navigation does not.

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

## Discovery and strategy activity

Scanner includes QMD's reusable market, news, SEC, and model discoveries. It
may show the strongest active signal on each security and expose the canonical
QMD lifecycle events through its Signals view. Canvas never derives a second
signal stream from table values.

Strategy Activity is a different abstraction. It queries durable strategy and
strategy-decision journal records and projects their event time, Strategy Run,
Strategy Profile revision, account, ticker, event type, action, score,
confidence, reason, and source. It can be filtered without changing the
underlying record. Broker execution and portfolio events remain in their own
authoritative containers.

The scanner market universe remains separate from the event stream. A scanner
row may expose its strongest active QMD signal and active-signal count, while
`signal_rows` carries newest-first lifecycle events. Active signals must never
replace the scanner universe.

See [QMD market-signal architecture](../architecture/QMD_SIGNAL_ARCHITECTURE.md)
for the authority matrix, lifecycle schema, causal clock, and strategy contract.

## Watch Universe ownership

A Watch Universe is selected by a Run Plan and resolved before the Run Plan
creates or retires ticker campaigns. Canvas displays that configured universe,
its source, its linked Run Plans, and the resolved members projected onto the
Scanner snapshot. Canvas does not own or edit membership. Configured-symbol
universes are immediately resolvable; scanner-view and external-list sources
remain fail-closed until their point-in-time resolver is registered.

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
- Scanner and Watch Universe body rows use one fixed 42 px logical height before global UI scaling. Logos render at 28 px inside a 38 px identity cell with a 6 px leading inset, while News and SEC recency glyphs render at 15 px; rows without either asset retain the same height and alignment.
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

The dedicated `GET /api/trading/canvas-scanner` route supplies Scanner and the
market projection used for resolved Watch Universe members independently of
the broad Canvas preview request. `GET /api/trading/strategy-activity` supplies
durable Strategy Activity records independently of market discovery. An
unrelated QMD History coverage failure therefore cannot replace a valid
persisted scanner snapshot with a six-symbol sample or an empty universe. News
and SEC enrichments are attached in batch at the same clock and report their
failures separately from market-state availability.

Live consumers use `GET /api/trading/canvas-market-signals/{symbol}` and
`WS /api/trading/canvas-market-signals/stream/{symbol}` for ticker-bounded
canonical signal state. These endpoints proxy QMD lifecycle rows; they do not
derive a second browser-owned signal.

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
