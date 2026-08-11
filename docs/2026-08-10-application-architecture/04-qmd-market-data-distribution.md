[Previous: Data authority and storage](03-data-authority-and-storage.md) · [Architecture home](README.md) · [Next: Enrichment registry](05-enrichment-and-field-registry.md)

# QMD market-data distribution

## Product boundary

QMD is one market-data product implemented by two processes and one shared Rust
library:

- `qmd-gateway`: live acquisition, current memory, recent `q_live` persistence,
  live subscriptions, and recent repair;
- `qmd-history-gateway`: read-only bounded historical/recent queries, Replay,
  Backtest, and derived-cache construction;
- `qmd_core`: event decoding, bar algebra, calculations, market signals,
  product schemas, and source-independent causal state.

Clients ask QMD for a time range and product. They do not select a database or
decide which process/table contains a date.

## Three-source event distribution

```mermaid
flowchart TD
    A["Requested ticker set and event-time window"] --> B["QMD Source Planner"]
    B --> C["Current live memory and Massive tail"]
    B --> D["Recent durable q_live.events"]
    B --> E["Historical market_sip_compact.events_YYYY"]
    C --> F["Canonical event merge"]
    D --> F
    E --> F
    F --> G["Order, identity, coverage, overlap, and gap validation"]
    G --> H["Events, bars, charts, Scanner, Replay, Backtest, or model input"]
```

### Source tiers

| Tier | Normal range | Authority | Purpose |
| --- | --- | --- | --- |
| Current | after the newest durable QMD watermark through now | `qmd-gateway` memory plus canonical Massive tail | Partial bars, newest events, lowest-latency live state |
| Recent | current session plus configured three prior market sessions | `q_live.events` and `q_live.intraday_family_bars_v2` | Restart, warm-up, recent charts, recent repair |
| Historical | normally complete through the archive publication watermark, often day-before-yesterday or earlier | `market_sip_compact.events_YYYY` and completed bar tables | Long-range charts, Replay, Backtest, research |

Dates are operational expectations, not the routing authority. Coverage
manifests and source watermarks decide the actual boundary. If archive coverage
is late, recent data remains authoritative until the handoff audit passes.

## Source planner contract

The planner returns a `MarketSourcePlan`:

```text
request identity
normalized ticker identities and validity intervals
ordered non-overlapping source segments
source database/table/process per segment
event encoding and schema versions
coverage state per segment
archive and recent watermarks
overlap/deduplication policy
expected continuation cursor
```

Rules:

1. Split multi-year archive requests by `events_YYYY` table.
2. Split recent overlap at a verified coverage boundary, not midnight by
   assumption.
3. Prefer archive rows for a closed interval only after equivalence is proven;
   otherwise prefer recent canonical rows and report archive lag.
4. Use one stable event identity/ordinal rule to remove overlap duplicates.
5. Preserve strict `(event_time, source_sequence, event identity)` order.
6. Return explicit partial/gap evidence instead of silently shortening a range.
7. Current memory appends only events newer than the durable continuation
   watermark. Warm-up rows update state but are not emitted as new live events.

## Physical process interaction

**Target:** the backend has one QMD client. The QMD History source resolver can
query recent and archive ClickHouse tiers; for the current tail, the backend or
QMD distribution facade composes a live QMD continuation. Long-running Replay
pins a source plan and revision at creation, while Live charts may advance their
tail watermark.

**Current:** QMD History plans and reads archive plus verified recent
`q_live.events` segments. For compact-event windows, the typed backend QMD
client now consumes the plan's current-live segment from QMD Gateway, filters it
to the exact segment, orders and deduplicates the combined rows, and applies the
requested head/tail limit. Current-window chart and historical Scanner products
still need equivalent live-continuation composition.

## Retention and archive handoff

```mermaid
flowchart TD
    A["Live event accepted and encoded"] --> B["Persist q_live.events and recent family bars"]
    B --> C["Record recent coverage"]
    C --> D["Historical flatfile pipeline publishes archive coverage"]
    D --> E["Compare counts, bounds, event identities, ordering, and schemas"]
    E --> F{"Equivalent and complete?"}
    F -->|No| G["Retain q_live rows and report blocked retention"]
    F -->|Yes| H["Advance archive handoff watermark"]
    H --> I["Delete only q_live partitions older than retention"]
```

`download_update_events.py` and the Market SIP pipeline own historical flatfile
publication. QMD owns the handoff audit and deletion of QMD-owned recent tables.
Neither path copies recent rows directly into archive event tables as a shortcut.

## Bar hierarchy

### Intraday

The base durable live bar contract is the sparse three-family table:

```text
trade
quote_bid
quote_ask
```

The production grid is `100ms, 1s, 5s, 10s, 30s, 1m, 5m, 1h`. All resolutions
use fixed `[04:00, 20:00)` New York session buckets. Higher intraday resolutions
are algebraic rollups; they do not issue independent raw-event queries.

### Daily-session authority

`market_sip_compact.daily_session_bars_by_symbol_time_v1` is the completed
historical daily authority. It contains three scheduled session segments per
ticker/date—premarket, regular, and after-hours—with sufficient statistics and
point-in-time identity.

For the current/recent tier, QMD forms the same daily-session contract from
recent family bars. A completed recent day can be served from a versioned recent
daily cache until archive daily coverage takes authority.

### Weekly, monthly, and yearly

```mermaid
flowchart TD
    A["Canonical completed daily-session bars"] --> B["One full-day family projection"]
    B --> C["Weekly aggregation"]
    B --> D["Monthly aggregation"]
    B --> E["Yearly aggregation"]
    F["Current partial day"] --> G["Current partial week/month/year"]
    C --> G
    D --> G
    E --> G
```

Weekly, monthly, and yearly bars are derived from authoritative daily rows, not
rescanned from raw events. The response states `partial`, `closed`, or
`corrected`. Calendar grouping uses New York session dates. Internal monthly
identity is `1mo`; browser presentation may use `1M`.

The legacy `macro_bars_by_time_symbol` remains a migration/training artifact,
not the application candle authority. A future materialized macro cache must be
derived from the daily authority and carry its source revision.

## Chart and computation products

Backend consumers now express chart, compact-event, and Scanner reads as a
typed `QmdProductRequest`. A request without a causal window resolves to QMD
live; a timezone-aware `start`/`end`/`as_of` window resolves to QMD History.
The planner owns the approved endpoint and rejects ambiguous live-plus-history
requests, so route handlers do not select `q_live` or archive tables. QMD
History remains the owner of archive/recent source planning inside the
historical service.

When that plan contains `current_live`, compact-event reads are composed only
at the typed QMD client boundary. Non-tail forward reads fail closed if QMD
Gateway reports that their live arrival cursor was evicted; callers are not
given a silently truncated result. The existing array response remains a
compatibility projection, so a future envelope still needs to return the
combined plan and continuation evidence directly.

Live compact-event consumers now use a versioned, bounded per-ticker page. Its
arrival cursor, exact per-ticker eviction watermark, buffer bounds, `has_more`,
and delivery-order declaration make truncation explicit. The backend typed QMD
client uses this contract; the old raw-array endpoint remains only as a measured
compatibility surface. QMD Gateway also publishes a versioned latest
ticker-state envelope with authority, Scanner sequence, `as_of`, age, and
explicit ready/missing state. Its older nullable-row endpoint remains only as a
measured compatibility surface.

The same source plan supports:

- compact events and Tape/Quotes;
- family and condition bars;
- enriched chart bars;
- request-scoped indicators and structure;
- live and historical scanner frames;
- market-signal lifecycles;
- Strategy warm-up and observations;
- Replay, Backtest, and Debug event streams;
- live Market AI event/snapshot inputs;
- historical Market AI replay, model input, and certification reads.

Each response includes the source plan hash, source revisions, product schema,
calculation versions, coverage, `as_of`, and continuation cursor.

Paged event snapshots now pin the first page's source-plan hash and revision
token at the consumer. Every continuation echoes both values; QMD History
returns a typed 409/restart instruction if either changed. This prevents a
Replay or Backtest from silently blending pages across revisions. It detects
revision drift but does not yet provide an old-revision physical snapshot, so a
conflict restarts instead of continuing against historical table state.

## Market AI delivery contract

```mermaid
flowchart TD
    A["QMD Gateway live stream and ticker snapshot"] --> D["Market AI live state"]
    B["QMD History bounded historical products"] --> E["Market AI replay and historical inference"]
    C["Registered bounded ClickHouse context query"] --> F["Frozen contextual evidence"]
    D --> G["Versioned model input context"]
    E --> G
    F --> G
```

QMD now provides stable live compact-event and ticker-state snapshots. The
Scanner stream reconnects through an authoritative snapshot and replaces client
state immediately after receiver lag or a non-contiguous sequence. Equivalent
repair contracts for the remaining raw event/product streams remain open. The historical request contract
uses the same `qmd_core` encoding and source plan. Market AI chooses the
requested product and causal cutoff; it does not choose `q_live` versus
`market_sip_compact` or reproduce boundary logic.

Direct ClickHouse access remains valid for registered contextual joins and
approved bulk/offline reads. If a direct read contains canonical market events,
it must use the QMD-owned schema/decoder and carry the same coverage and source
plan semantics as QMD History. QMD-derived bars, indicators, structure, and
signals should normally be requested from QMD History rather than recalculated
inside Market AI.

## Cache rules

Historical cache identity includes ticker set, event window, source-plan hash,
source revision, product schema, and calculation manifest. A cold build may
construct all configured bar resolutions once, but only explicitly requested
calculations are added. Live caches are bounded and ticker-sharded. Late events
increase revision and invalidate dependent cache entries.

## Failure behavior

- Archive lag: continue from verified recent coverage and report lag.
- Recent persistence lag: live trading may use accepted memory state only while
  explicit durability lag remains within configured policy; automatic entry
  fails closed beyond it.
- Current stream disconnect: report stale time and use REST repair only through
  the canonical ingestion path.
- Source overlap mismatch: do not delete recent data or blend inconsistent
  rows; quarantine the boundary.
- Missing segment: return partial coverage and block modes requiring complete
  history.

## Navigation

[Previous: Data authority and storage](03-data-authority-and-storage.md) · [Architecture home](README.md) · [Next: Enrichment registry](05-enrichment-and-field-registry.md)
