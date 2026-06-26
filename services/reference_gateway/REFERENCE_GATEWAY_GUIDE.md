# Reference Gateway Operating Model

The reference gateway is a continuously runnable, low-frequency maintenance
service for market reference data. It is not a high-frequency ingest gateway
like QMD. Its job is to keep the identity graph and tradability publications
safe for live trading, scanner setup, and model data joins.

The service can run during market hours. Market hours are not a blocker for
observation, auditing, issue discovery, or risk-reducing issue writes. Market
hours only block promotion-style writes that can change the active canonical
graph or rebuild the live tradable universe while trading components may be
using it.

## Responsibility Lanes

### 1. Provider Observation

Collect current evidence from outside systems without treating it as canonical
truth.

Current and planned sources:

- Massive active ticker list
- Massive ticker overview/reference metadata
- IBKR Client Portal contract/conid lookup
- FINRA consolidated short-volume files
- SEC fails-to-deliver files
- future borrow/easy-to-borrow source

Market-hours behavior:

- allowed to run
- allowed to save observations, reports, and open issues
- not allowed to promote ambiguous observations directly into canonical rows
- allowed to immediately block any currently tradable latest-universe row
  touched by a new open issue

Example:

Massive reports a new active ticker `ABCD`. The gateway can fetch the overview,
query IBKR candidates, and open a mapping issue during market hours. It should
not create a tradable `ABCD` row unless the candidate is clean and promotion is
allowed.

### 2. Identity Resolution

Convert provider observations into proposed canonical entities.

The resolver checks:

- issuer identity, preferably CIK/LEI/EIN
- security identity, preferably FIGI/share-class evidence
- listing identity, using security, exchange, and currency
- symbol identity, using ticker plus listing
- IBKR conid candidates for order routing
- exchange aliases from Massive/IBKR to canonical exchange codes

Market-hours behavior:

- allowed to classify and write issues
- allowed to auto-close deterministic stale issues
- should stage clean proposals
- should not promote canonical graph changes unless an explicit override is used

### 3. Issue Resolution

Issues are control-plane data, not passive logs. An unresolved blocking issue
must make the affected issuer/security/listing/symbol non-tradable.

The code resolves only cases with deterministic evidence. Ambiguous cases stay
open and block tradability.

When a new issue touches a row that is already tradable in the latest
`feature_tradable_universe_v1`, the gateway inserts a newer replacement row for
that same latest-universe key with `is_tradable = 0` and
`exclusion_reason = 'open_mapping_issue'`. This targeted block is allowed during
market hours because it reduces risk and does not promote any new instrument.

### 4. Canonical Graph Maintenance

Maintain source-of-truth reference rows:

- `id_issuer_v1`
- `id_issuer_identifier_v1`
- `id_security_v1`
- `id_security_identifier_v1`
- `id_listing_v1`
- `id_symbol_v1`
- `id_source_mapping_v1`

Market-hours behavior:

- normally blocked
- allowed only with an explicit market-hours override and reason

### 5. Tradability And Safety Policy

Build conservative tradability decisions.

A row is tradable only when all hard rules pass:

- active symbol
- active listing
- active security shape
- supported US stock/common-stock product type
- USD listing currency
- US exchange
- valid positive IBKR conid
- durable issuer identity
- no duplicate durable issuer identifier
- no open mapping issue touching the symbol/listing/security/issuer/ticker

If any condition fails, the row remains visible but is published as
`is_tradable = 0` with an exclusion reason.

### 6. Published Reference Features

Build stable tables consumed by scanner, live trading, and training jobs:

- `feature_tradable_universe_v1`
- `feature_scanner_static_v1`
- `id_sec_market_bridge_v1`
- market reference publication tables

Market-hours behavior:

- rebuilds are blocked by default
- after hours, the gateway can rebuild and audit publications

### 7. Audit, Coverage, And Repair

Continuously prove that data is coherent.

The audit checks:

- required table presence
- table group row availability
- weak issuer identity
- duplicate durable issuer identifiers
- missing issuer parents
- missing/invalid IBKR conids
- open mapping issues
- unsupported US stock shapes
- missing latest tradable-universe rows
- hard-rule violations in tradable publications
- market publication recency

## Issue Resolution Classes

### Automatically Resolvable

The gateway can close the issue without human input because canonical evidence
now exists and is unambiguous.

Implemented examples:

1. `massive_active_ticker` now exists as a valid canonical symbol.

   Error example:

   Massive listed ticker `ABCD`, but `id_symbol_v1` did not contain an active
   primary row with a valid listing and conid, so the gateway opened an issue.

   Resolution:

   A later run finds `ABCD` in `id_symbol_v1` joined to an active USD US stock
   listing with a positive IBKR conid. The resolver inserts a resolved evidence
   row and deletes the old open issue row.

2. `weak_issuer_identity` now has a durable identifier.

   Error example:

   Issuer `issuer:market_reference:xyz` has active US stock candidates but no
   CIK, LEI, or EIN. The tradable universe blocks related rows with a weak
   identity exclusion.

   Resolution:

   A later SEC bridge or identifier import adds a CIK/LEI/EIN in
   `id_issuer_identifier_v1`. The resolver marks this deterministic condition
   as resolved and removes the open blocker.

### Auto-Block Until Resolved

The gateway cannot safely fix the issue, but it can safely block trading.

Examples:

- weak issuer identity still has no CIK/LEI/EIN
- valid IBKR conid is missing
- source ticker exists but required listing evidence is incomplete
- provider data is current but not enough to promote

Action:

- keep/open issue
- publish affected rows as `is_tradable = 0`
- wait for stronger evidence or after-hours repair

### Human Review Required

The gateway has conflicting plausible interpretations and must not guess.

Examples:

- IBKR returns multiple plausible US stock contracts
- CIK appears to map to multiple issuers
- Massive and IBKR disagree on security/exchange identity
- ticker rename, merger, ADR/common-stock confusion, or share-class ambiguity

Action:

- keep/open issue
- write compact evidence
- block affected rows
- require manual or stronger resolver decision

### Historical Repair

The issue no longer affects current trading, but historical joins or model
training can still benefit from repair.

Implemented example:

An old `weak_issuer_identity` issue points to an issuer that no longer has an
active US stock candidate. The resolver closes the current blocker as historical
housekeeping, because it should not keep blocking today's tradable universe.

## Why Resolution Deletes Open Rows

`id_mapping_issue_v1` is a `ReplacingMergeTree` ordered by `issue_status`.
Because `issue_status` is part of the sorting key, inserting a second row with
the same `mapping_issue_id` and status `resolved` does not replace the old
`open` row under `FINAL`.

Therefore the resolver uses this pattern:

1. insert a compact resolved evidence row
2. delete the matching open issue row with a synchronous mutation

This preserves a resolution record while making audit queries over open issues
correct.

## Continuous Operation Policy

### During Market Hours

Allowed:

- run audits
- poll Massive active tickers
- fetch Massive overview evidence
- query IBKR candidates when Client Portal is authenticated
- write discovered issue rows
- close deterministic stale issues
- block unsafe instruments through issue rows and targeted latest-universe
  replacement rows

Blocked by default:

- schema changes
- canonical graph promotion
- tradable/scanner publication rebuilds
- heavy market-publication historical gap fills

### After Hours

Allowed:

- promote clean canonical graph changes
- resolve issues
- rebuild tradable/scanner publications
- run recent market-publication gap fills
- run full audit and write reports

The service runs a startup preflight before normal work. It checks ClickHouse,
artifact storage, Massive reference API access when active ticker reconciliation
is requested, and IBKR Client Portal authentication when IBKR resolution is
enabled or required. If a required dependency is missing, the gateway exits
instead of running partial maintenance.

Daemon runs also write JSONL runtime logs:

```text
<market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
```

The daemon stops if a child maintenance cycle exits non-zero. A broken
dependency or failed publication step should not be hidden by the next loop.

## Common Commands

Continuous daemon:

```powershell
.\scripts\run_reference_gateway.ps1 -Execute -ActiveTickerCheck -Daemon
```

Controlled test-only guards:

```powershell
.\scripts\run_reference_gateway.ps1 -Execute -ActiveTickerCheck -Daemon -NoIbkrRequired
.\scripts\run_reference_gateway.ps1 -Execute -ActiveTickerCheck -Daemon -NoPreflight
.\scripts\run_reference_gateway.ps1 -Execute -ActiveTickerCheck -Daemon -NoImmediateTradabilityBlock
```

One-shot after-hours maintenance:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -WriteDatabase q_live -Execute -EnsureMarketPublicationSchema -ActiveTickerCheck
```

Read-only audit:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -WriteDatabase q_live
```

Temp database test:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -TestWriteDatabase q_reference_tmp -Execute -EnsureMarketPublicationSchema -MarketHoursWriteOverride -MarketHoursWriteReason "temp reference gateway test"
```
