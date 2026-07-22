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
- News and SEC
- Signals
- Signal event

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
- Scanner identity is fixed at the left edge of the selected schema. A narrow, unlabeled first column contains only the provider logo when one exists; a missing logo leaves that cell blank. The adjacent Symbol cell contains the ticker followed by compact company-news and SEC recency icons. Missing recent events leave no placeholder ornament.
- Event icons have no badge background or border: company News uses a filled flame, SEC uses the filing-check mark, hot events use the danger color, and cold events use the information color. Old and absent events render no ticker-cell icon. The News icon is restricted to classified company news, so broad market or editorial coverage cannot mark a ticker.
- Compact News and SEC badge columns sit at the right edge of every default schema so market-state comparisons stay adjacent. They contain explainable classifications (for example, news topics and SEC form classes), not duplicate recency icons. Their exact labels and independent hot/cold states are available in the table filter.
- Selectable rows retain the normal pointer and use a quiet selection tint on hover. Selecting with pointer or keyboard assigns the list container the first unused Canvas link color, creates a dedicated Chart on the same link, and applies the selected symbol. Later selections reuse and focus that exact linked Chart; closing it does not discard the pairing, so the next selection reopens it instead of taking over an unrelated chart.
- The column picker exposes source coverage for batch-projected reference fields. It does not advertise fields whose canonical materialized authority is empty or unavailable.
- Positive and negative market values use semantic theme colors; neutral and unavailable states stay visually distinct.

## Source and scale behavior

QMD live scanner state is the live cross-sectional authority. Historical and replay screens use a causally materialized full-universe snapshot from `q_live.canvas_historical_scanner_v1`:

1. The first request for a market clock performs one set-based aggregation over the compact SIP event partitions. It does not fan out into per-ticker requests.
2. The result is stored by snapshot clock, lookback, schema version, and source revision.
3. Later requests reuse the stored rows while the compact-event continuity revision is unchanged.
4. A changed upstream revision causes a new snapshot revision to be written; older rows remain auditable.

The dedicated `GET /api/trading/canvas-scanner` route makes the Scanner, Watchlist, and market-derived Signal Stream independent of the broad Canvas preview request. An unrelated QMD History coverage failure therefore cannot replace a valid persisted scanner snapshot with a six-symbol sample or an empty universe. News and SEC enrichments are attached in batch at the same clock and report their failures separately from market-state availability.

News and SEC enrichment is batch-linked to ticker identity. The scanner uses ticker-aggregated queries over the complete causal news and filing windows rather than reusing the 30-item All News/All SEC preview queries. Company-news classification happens before ticker aggregation; SEC aggregation uses the event-valid CIK-to-market bridge. Identity, issuer, country, market-cap, share-supply, float, and short-interest are resolved causally for the entire tradable universe. The same set-based projection attaches the current canonical logo asset as non-market presentation metadata. Every market and filing source is bounded by the Canvas clock, including filing publication availability and reference-table insertion time. The table never issues per-row fact requests. Field coverage is returned with the snapshot so users can distinguish a partially published source from a broken column.

Canvas charts always read the QMD History contract through `GET /api/trading/canvas-chart/history`, including when the selected Canvas clock is close to wall time. The live QMD REST/websocket contract is owned only by the Live Trading workspace. This prevents a historical scanner selection from silently changing data authority based on clock proximity.

Historical scanner rows carry the canonical logo URL returned by their set-based reference projection, avoiding a second ticker-name/path inference query. The bounded presentation endpoint remains a fallback only for strategy-owned symbols that are not in the scanner projection. An unavailable presentation database is returned as retryable service state; it is not cached as proof that the ticker has no logo.

Logo binaries are served from the same `REFERENCE_GATEWAY_PRESENTATION_ASSET_ROOT_WIN` authority used by the reference gateway writer. `REAL_LIVE_LOGO_ARTIFACT_ROOT` remains the explicit serving override; the obsolete trading-dashboard artifact root is accepted only as a legacy final fallback.
