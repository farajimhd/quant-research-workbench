# Reference Gateway

The reference gateway owns slow-changing market reference data:

```text
issuer -> security -> listing -> symbol -> tradability publications
```

It is separate from QMD, news, and SEC. QMD streams market events, news streams
Benzinga articles, and SEC streams filings/XBRL. The reference gateway keeps the
identity graph, broker conids, market publication data, and tradability outputs
current enough for scanner setup, live trading, and training joins.

It also owns the ongoing SEC-to-market bridge `q_live.id_sec_market_bridge_v1`.
SEC ingestion writes raw filing/text/XBRL rows only; downstream services such as
`text_embed_gateway` read this bridge to convert SEC CIK/accession events into
the same ticker-aligned context schema used by historical training data.

The broader fact-table schemas are present for compact downstream publications,
but share supply, news, SEC, short pressure, borrow, country, and similar
dimensions are query-first unless a dedicated materialized publication is
explicitly enabled. The trading app should use the security-dimension query
layer for those dimensions.

For the detailed operating model, read:

```text
services/reference_gateway/REFERENCE_GATEWAY_GUIDE.md
```

For the reviewed startup/control flow, read:

```text
services/reference_gateway/REFERENCE_GATEWAY_FLOW_REVIEW.md
```

## Hard Tradability Rule

Any unresolved issue means the security is not tradable.

A row can enter `feature_tradable_universe_v1` as `is_tradable = 1` only when
all required relationships are resolved and unambiguous:

- active source symbol
- active listing
- active security
- supported US stock/common-stock shape
- USD listing currency
- US exchange
- valid positive IBKR conid
- durable issuer identity when required
- no duplicate durable issuer identifier
- no open mapping issue touching the issuer/security/listing/symbol/ticker
- no ambiguous IBKR contract match
- no unresolved exchange mapping

If any check fails, the row may remain visible for review, but it must publish
as `is_tradable = 0` with an `exclusion_reason`.

## Public Controls

The gateway exposes high-level operator knobs, not individual task switches.

| Knob | Values | Default | Purpose |
| --- | --- | --- | --- |
| `-Mode` | `Prod`, `Temp` | `Prod` | `Prod` reads/writes `q_live`; `Temp` reads `q_live` and writes `q_reference_tmp`. |
| `-Run` | `Daemon`, `Once` | `Prod=Daemon`, `Temp=Once` | Process lifetime. |
| `-Integrity` | `Strict`, `ReportOnly` | `Strict` | `Strict` writes issues/blocks tradability; `ReportOnly` audits without guardrail writes. |
| `-Maintenance` | `Auto`, `Skip`, `Force` | `Auto` | Heavy/promotion work policy. |
| `-MaintenanceReason` | text | empty | Required for `-Maintenance Force` in production. |
| `-Diagnostics` | `None`, `Rules`, `TableGroups`, `Config` | `None` | Print a read-only diagnostic view and exit. |

## Commands

Production daemon:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Temp write test:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

One-shot production pass:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

Skip heavy maintenance while still running source sync and integrity:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Skip
```

Force maintenance with an auditable reason:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once -Maintenance Force -MaintenanceReason "reviewed after-hours repair"
```

Diagnostics:

```powershell
.\scripts\run_reference_gateway.ps1 -Diagnostics Rules
.\scripts\run_reference_gateway.ps1 -Diagnostics TableGroups
.\scripts\run_reference_gateway.ps1 -Diagnostics Config
```

Equivalent Python entrypoint:

```powershell
python -m services.reference_gateway.main --mode prod --run once --integrity strict --maintenance auto --diagnostics none
```

## Operating Policy

Operational runs always include source sync. Massive active tickers are compared
against the canonical graph, Massive overview evidence is fetched for new
candidates, and IBKR Client Portal is queried for conid evidence. IBKR is
required because unresolved conids are trading blockers.

Market hours are evaluated through the shared Massive-backed service policy:
`/v1/marketstatus/now` supplies the current active/closed state and
`/v1/marketstatus/upcoming` supplies full closures and early closes. If Massive
is unavailable, the policy falls back to the local New York extended-hours
schedule.

During market hours:

- source sync runs
- audits run
- deterministic issue resolution can run
- new issue rows can be written
- immediate `is_tradable=0` replacement rows can be written
- canonical graph promotion and heavy publication work are deferred in
  `Maintenance=Auto`

After hours:

- clean canonical graph promotions can run
- SEC bridge plus tradable/scanner publications can be rebuilt
- coverage-aware market publication gap fill can run from the configured deep
  backfill start date
- schema upkeep can run

`Maintenance=Force` allows maintenance work during an active window only with a
reason. `Maintenance=Skip` disables maintenance while leaving source sync and
integrity guardrails active.

## Source Schedules

Operational source sync always runs, but expensive provider jobs are gated by
`market_reference_source_schedule_v1`:

- `massive_ticker_details`
- `ibkr_borrow_availability`
- `country_assertions`
- `market_publication_gap_fill`

The schedule table is DB-backed so daemon restarts do not lose cadence state.
Tune cadence with:

```text
REFERENCE_GATEWAY_CURRENT_TICKER_DETAIL_FREQUENCY_SECONDS=86400
REFERENCE_GATEWAY_IBKR_BORROW_FREQUENCY_SECONDS=1800
REFERENCE_GATEWAY_COUNTRY_ASSERTION_FREQUENCY_SECONDS=86400
REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_FREQUENCY_SECONDS=3600
```

## Autonomous Publication Maintenance

When maintenance is allowed, the gateway runs the coverage-aware publication
worker with `--resume-from-coverage`. The default deep backfill start date is
2019-01-01, so large historical gaps are handled by the service instead of
requiring a manual workstation command.

The gateway also bootstraps coverage from existing migrated publication tables
for short interest, Reg SHO threshold, presentation assets, and Massive flatfile
inventory. Bootstrap coverage records that existing rows were inspected; it
does not fabricate provider rows.

## Memory Guardrail

Each child cycle writes memory snapshots to the runtime JSONL log. The parent
daemon also records its memory after each child cycle exits. Optional controls:

```text
REFERENCE_GATEWAY_DAEMON_CHILD_TIMEOUT_SECONDS=7200
REFERENCE_GATEWAY_DAEMON_CHILD_MAX_RSS_MB=0
```

If `REFERENCE_GATEWAY_DAEMON_CHILD_MAX_RSS_MB` is non-zero and the child exits
above that RSS limit, the cycle fails instead of silently continuing.

## Outputs

Reports:

```text
<market-data>/prepared/reference_gateway/reports
```

Runtime JSONL logs:

```text
<market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
```

## Environment

Useful operational env values:

```text
REFERENCE_GATEWAY_MODE=prod
REFERENCE_GATEWAY_RUN=daemon
REFERENCE_GATEWAY_INTEGRITY=strict
REFERENCE_GATEWAY_MAINTENANCE=auto
REFERENCE_GATEWAY_DIAGNOSTICS=none
REFERENCE_GATEWAY_ACTIVE_TICKER_PAGE_LIMIT=1000
REFERENCE_GATEWAY_ACTIVE_TICKER_MAX_PAGES=1000
REFERENCE_GATEWAY_ACTIVE_TICKER_NEW_CANDIDATE_LIMIT=250
REFERENCE_GATEWAY_DAEMON_ACTIVE_INTERVAL_SECONDS=900
REFERENCE_GATEWAY_DAEMON_AFTER_HOURS_INTERVAL_SECONDS=3600
REFERENCE_GATEWAY_DAEMON_CHILD_TIMEOUT_SECONDS=7200
REFERENCE_GATEWAY_DAEMON_CHILD_MAX_RSS_MB=0
REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_DAYS=14
REFERENCE_GATEWAY_MARKET_PUBLICATION_DEEP_BACKFILL_ENABLED=true
REFERENCE_GATEWAY_MARKET_PUBLICATION_DEEP_BACKFILL_START_DATE=2019-01-01
REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_FREQUENCY_SECONDS=3600
```

Deployment-specific database env overrides are still supported:

```text
REFERENCE_CLICKHOUSE_READ_DATABASE=q_live
REFERENCE_CLICKHOUSE_WRITE_DATABASE=q_live
```

Temp mode normally uses `q_reference_tmp` without requiring those env values.

## Validation

Fast local smoke test:

```powershell
python -m services.reference_gateway.smoke_test
```

Parser and diagnostics checks:

```powershell
python -m services.reference_gateway.main --help
python -m services.reference_gateway.main --diagnostics rules
python -m services.reference_gateway.main --diagnostics table-groups
python -m services.reference_gateway.main --diagnostics config
```
