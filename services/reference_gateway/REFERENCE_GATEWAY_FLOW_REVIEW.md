# Reference Gateway Flow Review

This document tracks the reviewed public flow for the reference gateway. It is
kept self-contained so each step can be reviewed without reading the Python
source first.

## Review Comment Ledger

| ID | Stage | Comment | Status |
| --- | --- | --- | --- |
| C1 | Startup | CLI flags should not be converted into environment variables before `ReferenceGatewayConfig` is built. | Fixed |
| C2 | Docs | The guide must explain every argument/config value before asking for comments. | Fixed |
| C3 | CLI | Routine operation needs one prod knob and one temp/debug knob. | Fixed |
| C4 | Objectives | Explain the gateway by objectives: source sync, integrity, maintenance, and observability. | Fixed |
| C5 | CLI | Remove stale defensive knobs. IBKR is required for conid resolution and should not have a bypass flag. | Fixed |
| C6 | CLI | Active ticker sync is an internal source-sync task, not a public flag. | Fixed |
| C7 | CLI | Replace low-level write switches with high-level operator knobs. | Fixed |
| C8 | Stage 2 | Add the real runtime validation sequence before moving to UI/terminal polish. | Added |
| C9 | Stage 3 | Add live Rich status panels and structured runtime events for operator visibility. | Fixed |
| C10 | Flow Stage 1 | Process entry and mode resolution reviewed and accepted. | Accepted |
| C11 | Flow Stage 2 | Preflight and dependency gate reviewed and accepted. | Accepted |
| C12 | Flow Stage 3 | Write policy and market-hours decision reviewed and accepted. | Accepted |
| C13 | Flow Stage 4 | Audit and current-state read reviewed and accepted. Audit warnings/errors must be visible as grouped terminal aggregates plus recent/high-priority messages. | Accepted |
| C14 | Flow Stage 5 | Source sync is the core service function. It must run on predefined provider/data-domain frequencies and sync active-ticker data from Massive, IBKR, SEC, FINRA, and future providers without using low-level operator flags. | Added |
| C15 | Flow Stage 5 | Provider contracts must list exactly what is received from each provider, how it is used, and what creates non-tradable issues. | Added |
| C16 | Flow Stage 6 | Add a universal alert table design. The reference gateway monitors normalized news, SEC, Massive, FINRA, IBKR, and internal reference events, emits configured alerts, and gives consumers enough labels/groups to build detailed strategies. | Added |
| C17 | Flow Stage 6 | Consumer services are external to the reference gateway. The reference gateway may also read its own emitted alerts for internal repair, maintenance, and publication decisions, but consumer execution belongs in downstream services. | Added |
| C18 | Flow Stage 7 | Add canonical security fact tables aligned with alert families/groups. Avoid redundant storage: source tables keep provider detail, fact tables keep compact normalized history, and trading publications keep latest pre-joined rows. | Added |
| C19 | Flow Stage 7 | Add the fact-layer flow position and initial-fill design. Separate deterministic DB-only fillers from workstation/LLM batching so cheap SQL fills are not blocked by expensive text/model extraction. | Added |

## Reviewed Flow Stages

This section is the stage-by-stage operating flow under active review. Each
stage should be reviewed before the next stage is finalized.

## Stage 1: Process Entry And Mode Resolution

This stage answers: when the reference gateway command starts, what mode does
it enter and what databases can it touch?

User-facing entry commands:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
.\scripts\run_reference_gateway.ps1 -Mode Temp
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Skip
.\scripts\run_reference_gateway.ps1 -Mode Prod -Maintenance Force -MaintenanceReason "reviewed repair"
```

### Mode

`Prod`:

- reads from `q_live`
- writes to `q_live`
- default run mode is `Daemon`
- intended for real service operation

`Temp`:

- reads from `q_live`
- writes to `q_reference_tmp`
- default run mode is `Once`
- intended for validation/testing without production writes

### Run

`Daemon`:

- starts a parent process
- parent loops
- each cycle starts a one-shot child run

`Once`:

- runs one gateway cycle
- exits after the cycle finishes or fails

### Integrity

`Strict`:

- writes discovered issue rows
- resolves deterministic stale issues
- immediately blocks unsafe instruments by publishing non-tradable replacement
  rows when needed

`ReportOnly`:

- inspects and reports
- does not write guardrail rows

### Maintenance

`Auto`:

- runs maintenance only when policy allows

`Skip`:

- skips maintenance
- still runs source sync and integrity checks

`Force`:

- runs maintenance with an explicit reason
- production force requires `-MaintenanceReason`

### Stage 1 Rules

- Source sync is not optional in an operational run.
- IBKR is required because conid resolution is part of source sync.
- Low-level switches such as active ticker check, write canonical graph, and
  rebuild tradable are not public controls. They are internal consequences of
  mode, integrity, and maintenance policy.

Review status: accepted.

## Stage 2: Preflight And Dependency Gate

This stage answers: before the gateway does useful work, what must be
available, and what happens if something is missing?

Preflight runs at the start of every operational run:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
.\scripts\run_reference_gateway.ps1 -Mode Temp
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

The gateway should check dependencies before source sync, audit writes,
maintenance, or publication updates begin.

### ClickHouse

Required because the gateway must read the canonical reference graph and write
issues/updates when allowed.

Checks:

- ClickHouse endpoint is reachable
- read database exists, usually `q_live`
- write database exists, either `q_live` or `q_reference_tmp`
- required reference tables exist or can be created if maintenance/schema
  policy allows it

Failure behavior:

- gateway exits
- gateway must not continue in partial mode

### Artifact/Data Root

Required because reports, runtime logs, plans, and generated scripts must be
written somewhere predictable.

Expected root:

- workstation: `D:/market-data`
- laptop/remote: `\\DESKTOP-SAAI85T\Workstation-D\market-data`

Failure behavior:

- gateway exits
- gateway must not write code or data into random fallback folders

### Massive API

Required for source sync.

Used for:

- active ticker list
- ticker overview/reference metadata
- detecting new or changed Massive-side symbols

Failure behavior:

- gateway exits
- gateway must not run source sync from stale data

### IBKR Client Portal

Required because new tradable candidates need conid resolution.

Used for:

- searching ticker candidates
- filtering to US stock/USD contracts
- identifying ambiguous or missing conid cases

Failure behavior:

- gateway exits if IBKR Client Portal is unavailable or unauthenticated
- gateway must not create or promote candidates that lack conid evidence

### Runtime Log Initialization

Required for observability.

Runtime log path:

```text
<market-data>/prepared/reference_gateway/logs/<run_id>/reference_gateway_events.jsonl
```

Failure behavior:

- if runtime logging cannot be initialized, that is a startup failure
- the gateway should not run a maintenance service with no durable runtime
  trace

### Stage 2 Output

If preflight passes:

- gateway proceeds to Stage 3
- terminal shows dependency status as OK
- JSONL log records preflight status

If preflight fails:

- gateway exits non-zero
- terminal shows the failed dependency
- JSONL log records the failure if logging was initialized

### Stage 2 Rules

- Preflight is strict for operational runs.
- There should not be a real-operation bypass such as `--no-preflight`.
- Diagnostics that do not require dependencies should live under
  `-Diagnostics`, not under operational modes.

Review status: accepted.

## Stage 3: Write Policy And Market-Hours Decision

This stage answers: after dependencies are OK, which writes are allowed right
now?

The gateway separates writes into three categories.

### Integrity Writes

Examples:

- write open mapping issues
- resolve deterministic stale issues
- publish immediate `is_tradable=0` blocks for unsafe instruments

Purpose:

- reduce trading risk
- make unsafe instruments non-tradable as soon as the issue is known

### Source-Sync Evidence Writes

Examples:

- write provider observations, plans, and reports
- write issue evidence derived from Massive, IBKR, or another source-sync
  provider

Purpose:

- keep provider drift visible
- preserve the evidence needed to diagnose and resolve mapping issues

### Maintenance/Promotion Writes

Examples:

- create or update schema
- promote clean candidates into canonical graph tables
- rebuild `feature_tradable_universe_v1`
- rebuild `feature_scanner_static_v1`
- run market publication gap fill

Purpose:

- update durable reference tables and derived publications
- perform heavier or broader changes that can reshape downstream views

### Market-Hours Policy

During market hours:

- integrity writes are allowed because they reduce trading risk
- source-sync evidence writes are allowed
- maintenance/promotion writes are blocked in `Maintenance=Auto`
- maintenance/promotion writes are allowed only with `Maintenance=Force` and a
  reason

Outside market hours:

- integrity writes are allowed
- source-sync evidence writes are allowed
- maintenance/promotion writes are allowed in `Maintenance=Auto`

With `Maintenance=Skip`:

- maintenance/promotion writes are skipped regardless of time
- source sync and integrity still run

With `Integrity=ReportOnly`:

- no issue writes
- no immediate tradability blocks
- audit/report only for integrity

### Stage 3 Rules

- Market hours should not block the service.
- Market hours only block risky maintenance/promotion work.
- The gateway should still detect problems during the market session.
- The gateway should still make unsafe instruments non-tradable during the
  market session.
- Any market-hours maintenance/promotion override must be explicit and
  auditable through `Maintenance=Force` plus `MaintenanceReason`.

Review status: accepted.

## Stage 4: Audit And Current-State Read

This stage answers: what does the gateway read from `q_live` to decide whether
the current reference data is safe enough for trading and source-sync work?

Audit runs after preflight and write-policy resolution. It reads current state
before source-sync changes are applied so the gateway has a clean baseline.

### Audit Inputs

The audit reads reference and publication tables from the configured read
database, normally `q_live`.

It checks:

- required tables exist
- parent/child relations are valid
- issuer identity quality is strong enough for tradability
- active US stock candidates have a valid tradable-universe state
- open mapping issues still exist
- unsupported US-stock shapes are excluded
- hard rule violations are not present in tradable publications
- market reference publication tables are present and recent enough for their
  implemented sources

### Audit Outputs

The audit produces:

- a JSON report under `<market-data>/prepared/reference_gateway/reports`
- a structured `audit_completed` runtime-log event
- terminal panels for status, grouped warnings/errors, and detailed findings
- a status used by later stages

The terminal must show audit warnings and errors in two ways:

- grouped aggregate counts by severity, including affected row counts
- several recent or high-priority messages so the operator can see what failed
  without opening the JSON report

### Meaning Of Audit Results

`ok`:

- structural checks passed
- no warning-level checks failed

`warning`:

- no structural error blocked the run
- at least one warning-level issue exists
- affected instruments may still be non-tradable depending on the check

`failed`:

- at least one error-level check failed
- the run may stop or later stages may be restricted depending on integrity
  mode

### Stage 4 Rules

- Audit is read-only.
- Audit never fixes data directly.
- Audit findings decide what source sync, issue resolution, immediate
  tradability blocking, or maintenance should do later.
- An audit warning can still mean an instrument is not tradable.
- An audit error means structural inconsistency and may stop or fail the run.

Review status: accepted.

## Stage 5: Source Sync And Provider Schedules

This stage answers: how does the gateway keep active ticker reference data
fresh, complete, and safe for trading?

Source sync is the most important operational part of this service. It is not
a one-off command and it is not controlled by a low-level public flag. It is a
scheduled set of provider/data-domain jobs that run at predefined frequencies.

### Source Sync Objective

The objective is to keep the reference graph aligned across providers:

- Massive active ticker universe
- IBKR tradability and conid evidence
- SEC issuer/company identity evidence
- FINRA short-sale or regulatory publication evidence
- future providers that add useful reference fields

The gateway should treat source sync as a recurring data freshness process.
Each provider/data domain has its own frequency because the source data updates
at different speeds and may have different rate limits.

### Provider Input Contracts

This section defines what the gateway expects to receive from each provider.
The exact provider response may contain more fields. The gateway should keep
only the compact fields needed for identity, tradability, publication, audit,
or issue evidence.

#### Massive Active Ticker List

Purpose:

- entry point for active ticker discovery
- detects new, missing, inactive, changed, or delisted symbols
- drives follow-up work in IBKR, SEC, FINRA, and publication syncs

Received from provider:

- `ticker`
- `name`
- `market`
- `locale`
- `primary_exchange`
- `currency_symbol`
- `cik`
- `composite_figi`
- `share_class_figi`
- `type`

Used for:

- ticker/symbol identity
- candidate exchange and currency
- first durable issuer hint through CIK
- security identity hint through FIGI
- security/ticker type classification

Creates issues when:

- ticker is active at Massive but missing from canonical `id_symbol_v1`
- CIK is missing for a candidate that needs durable issuer identity
- FIGI is missing for a candidate that needs durable security identity
- exchange cannot be mapped to canonical `ref_exchange_v1`
- currency is not USD for the current US-stock trading scope
- ticker type is unsupported for current tradability rules

#### Massive Ticker Overview

Purpose:

- enriches a new or changed Massive ticker with company/security context
- fills missing evidence from the active ticker list
- provides scanner/static-publication fields when owned by Massive

Received from provider:

- `ticker`
- `name`
- `active`
- `market`
- `locale`
- `primary_exchange`
- `currency_name`
- `cik`
- `composite_figi`
- `share_class_figi`
- `sic_code`
- `sic_description`
- `homepage_url`
- `branding.logo_url`
- `branding.icon_url`
- `list_date`
- `market_cap`
- `weighted_shares_outstanding`
- `share_class_shares_outstanding`

Used for:

- issuer display/name evidence
- CIK confirmation when active ticker row is incomplete
- FIGI confirmation when active ticker row is incomplete
- SIC code/description fields on issuer rows
- list-date and share-count/market-cap publication snapshots
- logo/icon discovery for presentation assets

Creates issues when:

- overview cannot be fetched for a new active ticker
- overview conflicts with active ticker evidence in a way that cannot be
  resolved deterministically
- overview says inactive while the active ticker list says active
- overview exchange/currency/FIGI/CIK conflicts with existing canonical rows

#### IBKR Client Portal Contract Search

Purpose:

- resolves broker routing evidence for order execution
- validates that a ticker has one compatible tradeable contract

Received from provider:

- `symbol`
- `conid`
- `secType` or `assetClass`
- `exchange`
- `listingExchange`
- `currency`
- `companyName` or `description`

Used for:

- `ibkr_conid` on listing rows
- broker routing eligibility
- exchange/currency cross-checks against Massive/canonical data
- ambiguity detection

Accepted for current scope only when:

- symbol exactly matches the candidate ticker
- conid is a valid positive identifier
- security type is compatible with US stocks
- currency is USD
- returned contract set has exactly one compatible candidate

Creates issues when:

- IBKR is unreachable or unauthenticated
- no compatible contract is returned
- multiple plausible compatible contracts are returned
- conid conflicts with an already accepted canonical listing
- IBKR exchange evidence cannot be mapped or conflicts with canonical exchange

#### SEC Identity And Filing Data

Purpose:

- validates issuer identity and durable company identifiers
- links securities/listings to SEC filing and XBRL evidence
- supports future fundamental and country/issuer assertions

Received from SEC-maintained data already handled by SEC pipelines/gateway:

- CIK
- accession number
- accepted timestamp
- form type
- filing/report period
- issuer/company name
- SEC entity metadata from submissions
- companyfacts/XBRL facts and units
- frame/fiscal-period references when available
- filing text and normalized filing metadata

Used for:

- issuer CIK confirmation
- SEC-to-market bridge rows
- issuer/company-name conflict checks
- XBRL-derived fundamentals used by downstream models
- country or domicile assertions when evidence is strong enough
- filing availability/recency checks

Creates issues when:

- a Massive active ticker has no SEC identity but should have one
- CIK maps to multiple issuers without a deterministic resolution
- SEC company identity conflicts with canonical issuer identity
- SEC filing/XBRL data cannot be linked to a known issuer/security/listing
- accepted timestamp is missing for post-2019 filings needed for market
  reaction alignment

#### FINRA And Regulatory Publication Data

Purpose:

- adds delayed regulatory/publication context for scanner and risk labels
- does not directly define canonical issuer/security identity

Received from FINRA or regulatory-publication jobs:

- provider ticker
- source venue or publication name
- trade/settlement/publication date
- short volume
- total volume when provided by the publication
- exempt short volume when provided
- short-interest quantity when available
- source event key
- source file/reference
- source content hash

Used for:

- `market_short_volume_v1`
- `market_short_interest_v1`
- short-pressure and short-crowding labels
- publication coverage checks
- scanner static fields that do not require real-time updates

Creates issues when:

- publication ticker cannot be mapped to a known active symbol/listing/security
- publication date is duplicated with conflicting values
- publication row is outside the expected provider coverage window
- source file/hash does not match a previously recorded coverage row

#### SEC Fails-To-Deliver And Reg SHO

Purpose:

- adds settlement-failure and threshold-list context for tradability/risk
  labels

Received from SEC/regulatory publication jobs:

- provider ticker
- CUSIP when available
- settlement date
- fails quantity
- issuer name
- previous close price when available
- threshold date
- listing exchange when available
- threshold status
- source event key/reference/hash

Used for:

- `market_fails_to_deliver_v1`
- `market_reg_sho_threshold_v1`
- scanner labels for settlement stress or threshold-list status
- issue evidence when CUSIP/ticker cannot link to canonical rows

Creates issues when:

- CUSIP/ticker maps to multiple securities
- publication row cannot be linked to a canonical symbol/listing/security
- regulatory publication coverage has a gap

#### IBKR Borrow Availability

Purpose:

- records broker-specific borrow/shortability state
- supports shortability labels but does not define market identity

Received from IBKR or broker borrow source:

- provider ticker
- IBKR conid
- observation timestamp
- borrow status
- shortable shares
- lender count
- indicative borrow rate
- fee rate

Used for:

- `market_security_borrow_v1`
- easy-to-borrow/hard-to-borrow labels
- broker-specific trading constraints

Creates issues when:

- borrow row has no matching conid/listing
- broker says non-shortable while a derived publication says shortable without
  source-specific explanation
- observation timestamp is stale beyond the configured borrow-data frequency

#### Massive Corporate Actions And Presentation Assets

Purpose:

- updates scanner/static publication fields and UI presentation data
- records market-structure events that can change symbol/listing interpretation

Received from Massive publication endpoints or synchronized artifacts:

- split date and split ratio
- cash dividend ex-date/pay-date/amount/currency
- IPO date/price/range/status when available
- logo/icon URL or downloaded presentation asset metadata
- snapshot fields such as market cap, price, volume, and share counts when
  used as a publication snapshot

Used for:

- `market_stock_split_v1`
- `market_cash_dividend_v1`
- `market_ipo_v1`
- `market_presentation_asset_v1`
- `market_security_market_snapshot_v1`
- `market_security_float_v1` when the field is the best available source

Creates issues when:

- corporate action ticker cannot be mapped
- split/dividend publication conflicts with existing canonical event rows
- presentation asset cannot be linked to a known active symbol
- snapshot date/source conflicts with another accepted source for the same
  field

### Active Ticker Sync

Active ticker sync is always part of an operational source-sync cycle.

It should:

- pull active ticker listings from Massive
- compare them with canonical symbol/listing/security rows in `q_live`
- skip Massive ticker candidates that already have an open
  `id_mapping_issue_v1` row for `source_entity_kind='massive_active_ticker'`
- identify new, missing, changed, inactive, or delisted candidates
- keep exchange/currency/security-type relationships consistent
- avoid promoting a candidate into tradable state until required provider
  evidence is complete

If a new active ticker appears, the gateway gathers the required evidence from
other providers before the ticker can become tradable.

Massive is the discovery source for tradable ticker candidates because QMD live
quotes and trades also come from Massive. IBKR is downstream routing evidence,
not an independent ticker discovery source. A ticker that exists only in IBKR is
not promoted by this service unless Massive also publishes it as an active stock
ticker.

### IBKR Contract Sync

IBKR is required for tradable US stock candidates because order routing needs a
valid conid.

For each candidate that needs IBKR evidence, the gateway should:

- query IBKR Client Portal for candidate contracts
- filter to supported US stock contracts
- require USD currency for the current trading scope
- handle multiple returned contracts as an ambiguity, not as success
- write an issue and keep the candidate non-tradable when conid resolution is
  missing or ambiguous

IBKR evidence is not the source of truth for issuer identity. It is routing
evidence for tradability.

### SEC Identity Sync

SEC data helps validate issuer/company identity.

For active tickers where SEC identity is applicable, the gateway should sync:

- CIK associations when available
- issuer/company names that help validate durable identity
- evidence that helps detect ticker/name changes or weak identity rows

SEC sync can run at a different frequency from Massive active ticker sync
because SEC reference data does not update with the same cadence.

### FINRA And Regulatory Publication Sync

FINRA and similar sources are publication-style providers. Their data may be
useful for static scanner fields, short-sale context, or regulatory labels.

These jobs should:

- run on their own publication-aware frequency
- update only fields or tables owned by the publication source
- avoid overwriting canonical graph identity fields directly
- write issue rows if the publication cannot be linked cleanly to a known
  active symbol/listing/security

### Provider Frequency Model

The gateway should have predefined sync frequencies by provider/data domain.

Examples:

| Sync Domain | Example Frequency | Reason |
| --- | --- | --- |
| Massive active ticker universe | frequent during market/premarket, less frequent after hours | new tickers, halts, or active-status drift affect scanner/tradability quickly |
| IBKR conid resolution | on demand for new or unresolved candidates, plus periodic retry | IBKR is needed only when a candidate needs routing evidence |
| SEC issuer identity | daily or after SEC refresh windows | issuer identity changes slower than market data |
| FINRA/regulatory publications | provider-specific publication schedule | publication data usually arrives on known delayed schedules |
| Derived tradable publications | after source sync and integrity checks, subject to maintenance policy | derived rows should reflect clean provider evidence |

The operator should not need to enable these one by one. The high-level
`Mode`, `Run`, `Integrity`, and `Maintenance` knobs decide whether the gateway
runs normally, in temp mode, once, or continuously. Provider schedules are
service configuration, not routine operator decisions.

### Source Sync Writes

Source sync may write:

- provider observation rows
- source-sync issue rows
- evidence needed to explain why a ticker is non-tradable
- candidate rows in temp/test mode

Source sync should not blindly overwrite canonical rows. If a provider
conflicts with current canonical data, the gateway writes an issue and keeps
the affected candidate non-tradable until the conflict is resolved.

### Current Startup Source-Sync Coverage

Startup source sync now calls the coverage-driven market-publication loader for
the implemented provider sources below. These jobs write source tables first;
canonical fact-table initial fill is a separate Stage 7 task.

Implemented startup-backed source rows:

- FINRA consolidated short volume into `market_short_volume_v1`.
- SEC fails-to-deliver files into `market_fails_to_deliver_v1`.
- Massive stock splits into `market_stock_split_v1`.
- Massive cash dividends into `market_cash_dividend_v1`.
- Massive IPO records into `market_ipo_v1`.
- Massive ticker details into `market_security_market_snapshot_v1` and
  share-supply rows in `market_security_float_v1`.
- IBKR point-in-time borrow/shortability fields into
  `market_security_borrow_v1`.

Still deferred because the authoritative provider contract or parser needs a
separate implementation:

- FINRA/exchange short interest ongoing sync.
- Reg SHO threshold-list sync.
- Massive presentation asset refresh.
- SEC-derived country assertions.

### Stage 5 Rules

- Source sync is always enabled for operational runs.
- Source sync is scheduled by provider/data domain.
- Provider frequencies are predefined service configuration.
- Massive is the active-ticker universe entry point.
- IBKR is required for conid/routing evidence, not issuer identity truth.
- SEC, FINRA, and future providers enrich or validate active ticker data on
  their own schedules.
- Missing, ambiguous, or conflicting required evidence makes the affected
  security non-tradable.
- Source sync writes evidence and issues during market hours when needed.
- Promotion into canonical graph/publication tables follows the Stage 3
  maintenance policy.

Review status: under review.

## Stage 6: Universal Alert Emission And Consumption

This stage answers: how do source changes become actionable, queryable alerts
without making every consumer understand every provider table?

The reference gateway should emit alerts into one normalized table when a
configured rule says a provider event or internal reference event is
interesting. The alert table is not only for user notifications. It is also the
invalidation and orchestration layer for recomputing derived features, blocking
tradability, prompting review, and notifying live-trading/scanner consumers.

### Alert Sources

The gateway can emit alerts from provider data it syncs directly and from
normalized data produced by other services.

Source examples:

- SEC gateway normalized filing, filing text, and XBRL rows
- News gateway normalized Benzinga/news rows and enrichment/classification rows
- Massive reference data, corporate actions, snapshots, IPOs, and presentation
  assets
- FINRA short-volume and short-interest publications
- IBKR conid, contract, and borrow/shortability evidence
- reference gateway audits, mapping issues, and tradability guardrails

The source services remain responsible for collecting and normalizing their own
raw data. The reference gateway watches their normalized outputs and emits
cross-domain alerts when configured conditions are met.

### Role In The Flow

Alert emission sits after a provider/source row has been normalized and before
any downstream service acts on it.

The reference gateway has two alert roles:

1. Emit normalized alerts from source sync, audits, provider observations,
   publication checks, and cross-source rules.
2. Use the same alert table for its own internal goals, such as deciding that a
   ticker needs after-hours repair, a derived publication must be rebuilt, or a
   tradability block must remain active until a mapping issue is resolved.

The reference gateway does not own every downstream reaction. Trading, scanner,
model-data, notification, and human-review consumers should live in their own
services or application modules. Those consumers read alerts and record their
own progress in the consumer-state table.

So the logical position is:

```text
source sync / audits / normalized SEC-news-Massive-FINRA-IBKR rows
-> reference gateway alert rule evaluation
-> market_reference_alert_v1
-> reference gateway internal maintenance decisions
-> external consumer services
```

### Alert Rule Catalog

Alerts should be emitted only for predefined rule types.

The system should keep a rule catalog, either as code-backed constants or a
small table such as `reference_alert_rule_catalog_v1`.

Each rule should define:

- `alert_type`
- `alert_subtype`
- provider/source tables it reads
- entity scope: issuer, security, listing, symbol, provider ticker, or market
- trigger condition
- default severity
- default labels
- whether a feature recompute is required
- whether tradability can be affected
- whether human review may be required
- deduplication key fields

Routine operators should not enable individual low-level alert rules from the
wrapper command. Rule activation and frequency are service configuration.

### Main Alert Table

Proposed table:

```text
q_live.market_reference_alert_v1
```

This table should be append-only with deterministic alert IDs. If a later event
updates the same logical alert, insert a newer replacement row with the same
`alert_id` and a newer `inserted_at`.

Core identity:

```text
alert_id
alert_version
alert_family
alert_group
alert_type
alert_subtype
severity
status
```

Recommended families:

```text
reference_identity
tradability_guardrail
sec_filing
news_catalyst
share_supply
short_pressure
borrow_pressure
corporate_action
ipo_pipeline
market_publication
data_quality
provider_health
feature_invalidation
```

Recommended groups:

```text
identity_mapping
issuer_identity
security_identity
listing_symbol
conid_routing
shares_outstanding
float_estimate
offering_supply
insider_activity
short_interest
short_volume
fails_to_deliver
reg_sho
borrow_availability
split_dividend
ipo_terms
news_keyword
news_llm_classification
sec_form_type
xbrl_fact_change
publication_gap
coverage_gap
```

Source fields:

```text
source_system
source_provider
source_table
source_event_id
source_event_version
source_timestamp_utc
detected_at_utc
source_evidence_ref
source_content_sha256
```

Entity fields:

```text
issuer_id
security_id
listing_id
symbol_id
provider_ticker
cik
accession_number
ibkr_conid
```

Trading/action fields:

```text
direction
event_status
impact_scope
time_sensitivity
confidence_score
impact_score
requires_recompute
recompute_scope
affects_tradability
requires_review
```

Useful values:

```text
direction: bullish, bearish, neutral, mixed, supply_increase, supply_decrease, unknown
event_status: detected, announced, potential, completed, amended, withdrawn, confirmed, disputed
impact_scope: symbol, security, issuer, sector, market, provider_only
time_sensitivity: immediate, intraday, daily, delayed_publication, historical
recompute_scope: none, symbol, security, issuer, provider_ticker, market
```

Human-readable fields:

```text
title
message
primary_label
secondary_labels
consumer_groups
action_flags
```

`secondary_labels`, `consumer_groups`, and `action_flags` should be compact
arrays of strings, not large JSON payloads.

Processing fields:

```text
first_seen_at_utc
last_seen_at_utc
processed_at_utc
expires_at_utc
inserted_at
```

The alert table should not store full news text, filing text, SEC raw payloads,
or provider JSON. It should store references to normalized source rows and
artifacts through `source_evidence_ref`, `source_table`, `source_event_id`, and
hash fields.

### Consumer State Table

Consumers should not mutate the alert row itself for every strategy. A separate
consumer state table keeps per-consumer progress.

Proposed table:

```text
q_live.market_reference_alert_consumer_state_v1
```

Fields:

```text
consumer_id
alert_id
consumer_group
status
claimed_at_utc
processed_at_utc
last_error
attempt_count
inserted_at
```

This lets multiple consumers use the same alert stream:

- scanner feature recompute
- live-trading UI
- model feature builder
- tradability blocker
- human-review queue
- notification/terminal display
- reference gateway internal repair and maintenance planner

The last item is internal to the reference gateway. The others should be
implemented by the owning downstream service. For example, live trading should
not rely on the reference gateway process to exclude a ticker from an order
ticket. It should read the current tradability/publication state and relevant
alerts itself.

### Alert Emission Examples

SEC filing:

```text
alert_family = sec_filing
alert_group = sec_form_type
alert_type = sec_form_submitted
alert_subtype = S-3
time_sensitivity = immediate
requires_recompute = 1
recompute_scope = issuer
```

SEC XBRL share change:

```text
alert_family = share_supply
alert_group = xbrl_fact_change
alert_type = sec_xbrl_share_fact_changed
alert_subtype = EntityCommonStockSharesOutstanding
direction = supply_increase or supply_decrease
requires_recompute = 1
recompute_scope = security
```

News offering keyword:

```text
alert_family = news_catalyst
alert_group = news_keyword
alert_type = news_supply_keyword_detected
alert_subtype = registered_direct_offering
requires_recompute = 1
recompute_scope = symbol
requires_review = depends_on_confidence
```

Massive split:

```text
alert_family = corporate_action
alert_group = split_dividend
alert_type = stock_split_detected
alert_subtype = reverse_split or forward_split
requires_recompute = 1
recompute_scope = security
```

IBKR borrow:

```text
alert_family = borrow_pressure
alert_group = borrow_availability
alert_type = ibkr_borrow_status_changed
alert_subtype = hard_to_borrow
requires_recompute = 1
recompute_scope = listing
```

Reference mapping issue:

```text
alert_family = tradability_guardrail
alert_group = identity_mapping
alert_type = mapping_issue_opened
alert_subtype = ambiguous_ibkr_contract
affects_tradability = 1
requires_review = 1
recompute_scope = symbol
```

### Deduplication

`alert_id` should be deterministic.

Suggested key:

```text
source_system
source_table
source_event_id
alert_type
alert_subtype
issuer_id/security_id/listing_id/symbol_id/provider_ticker
source_timestamp_utc
```

For source rows without stable IDs, use a canonical content hash over compact
source fields, not full raw payloads.

### Consumer Strategy

Consumers should filter by:

- `alert_family`
- `alert_group`
- `alert_type`
- `alert_subtype`
- `severity`
- `time_sensitivity`
- `consumer_groups`
- `requires_recompute`
- `affects_tradability`
- `requires_review`
- entity fields such as `symbol_id`, `security_id`, `issuer_id`, or ticker

This gives consumers a detailed alert strategy without making each consumer
hard-code every provider table.

### Stage 6 Rules

- Alerts are emitted from normalized provider data, not raw payloads.
- Alert rows are compact and reference source rows/artifacts instead of
  duplicating full text or JSON.
- Alert rules are predefined and versioned.
- The alert table is append/replacing by deterministic `alert_id`.
- Per-consumer status belongs in a separate consumer-state table.
- Alerts can trigger feature recompute, UI display, tradability blocking, or
  human review.
- The reference gateway owns cross-provider alert emission and alert rule
  evaluation.
- The reference gateway may consume its own alerts only for reference-data
  repair, maintenance planning, publication rebuilds, and guardrail decisions.
- External consumer behavior belongs outside the reference gateway.
- Source services own raw ingestion and source-specific normalization.

Review status: under review.

## Stage 7: Canonical Security Facts And Trading Publications

This stage answers: after alerts identify that something changed, where should
the durable security-level information live, and what should the trading system
read?

The key rule is **no redundant copies of provider payloads**. The system should
not copy full SEC filings, news text, provider JSON, or wide source rows into
each fact table. Source tables keep provider-specific detail. Fact tables keep
compact normalized history. Trading publications keep latest joined rows for
fast scanner/live-trading reads.

### Data Layers

Layer 1: normalized source tables.

Examples:

- `sec_filing_v2`
- `sec_filing_text_v2`
- `sec_xbrl_company_fact_v1`
- `sec_xbrl_frame_observation_v1`
- `benzinga_news_normalized_v1`
- `benzinga_news_ticker_v1`
- `market_short_volume_v1`
- `market_fails_to_deliver_v1`
- provider-specific Massive, FINRA, and IBKR publication tables

These tables should preserve source meaning and enough evidence to audit the
row. They are not the shape the trading system should query repeatedly.

Layer 2: canonical fact tables.

These tables answer one narrow question about an issuer/security/listing/symbol
over time. They normalize source-specific rows into compact, queryable facts.
They should include:

```text
fact_id
issuer_id/security_id/listing_id/symbol_id where applicable
provider_ticker where applicable
source_system
source_table
source_event_id or source_event_key
observed_at_utc
effective_at_utc or effective_date
value fields specific to the fact
confidence_score when derived or inferred
source_evidence_ref
source_content_sha256
source_run_id
inserted_at
```

Layer 3: latest trading publications.

Examples:

- `feature_tradable_universe_v1`
- `feature_scanner_static_v1`
- future `security_trading_context_latest_v1`

The scanner and live trading page should read this layer by default. It should
be compact, pre-joined, and rebuilt or incrementally refreshed when alerts say
the underlying facts changed.

### Fact Table Redundancy Rules

- Do not store a fact table if the source table already has exactly the right
  entity keys, time semantics, and value semantics.
- Do create a fact table when multiple sources need to be reconciled into one
  meaning, such as float, share supply, borrow status, tradability, or SEC text
  events.
- Do not merge unrelated facts into one wide table just because the scanner may
  eventually display them together.
- Do not store full text or JSON in fact tables. Store source references and
  compact extracted values or labels.
- Do not make live trading perform wide joins across source tables. Reference
  gateway should publish latest compact rows for trading.
- Historical research/backtesting should use fact tables with as-of joins, not
  the latest publication table.

### Fact Catalog

The following catalog lists useful fact families based on the current provider
data and `q_live` schema.

| Fact family | Table | Existing state | What it means | Main sources | Alert alignment | Redundancy decision |
| --- | --- | --- | --- | --- | --- | --- |
| Tradability | `security_tradability_fact_v1` | Schema and filler implemented | Time-aware reason a security/listing/symbol is tradable or blocked. This is the durable reference history behind `is_tradable`; it does not include QMD live halt/LULD states. | reference audits, mapping issues, latest tradable universe, Massive active status | `tradability_guardrail/*` | New table is justified because `feature_tradable_universe_v1` stores latest state only. The filler writes only when the latest fact differs from current reference state. |
| Routing | `security_routing_fact_v1` | Schema and filler implemented | Broker routing evidence such as IBKR conid, selected contract, ambiguity status, and validity window. | IBKR CPAPI, listing/symbol graph | `tradability_guardrail/conid_routing` | New table is justified because conid evidence should be historized separately from listing identity. The filler writes only when routing status/conid/exchange/currency changes. |
| Share supply | `security_share_supply_fact_v1` | Schema implemented; filler pending | Shares outstanding, weighted shares, share-class shares, units, and source confidence. | SEC XBRL, Massive market snapshot | `share_supply/shares_outstanding`, `share_supply/xbrl_fact_change` | New table is justified because current source rows use different meanings and timestamps. |
| Float | `market_security_float_v1` or future `security_float_fact_v1` | Existing partial | Provider or derived estimate of freely tradable shares. | Massive overview, future SEC-derived float logic | `share_supply/float_estimate` | Reuse existing table if it can hold source tag, confidence, and evidence cleanly. Avoid a second float table unless semantics diverge. |
| Market snapshot | `market_security_market_snapshot_v1` | Existing | Provider snapshot values such as market cap, round lot, and shares from Massive. | Massive snapshot/reference endpoints | `market_publication/market_snapshot` | Existing table is enough; do not duplicate into a generic fact table. |
| Short interest | `market_short_interest_v1` | Existing | Exchange/FINRA short interest for a settlement date, plus days-to-cover when available. | FINRA/exchange short-interest publications | `short_pressure/short_interest` | Existing table is enough. Latest labels belong in scanner publication. |
| Short volume | `market_short_volume_v1` | Existing | Daily short-sale volume and ratio by venue/source. | FINRA short-volume files | `short_pressure/short_volume` | Existing table is enough. Do not duplicate into scanner except latest compact fields. |
| Fails to deliver | `market_fails_to_deliver_v1` | Existing | SEC fails-to-deliver quantity by settlement date. | SEC FTD files | `short_pressure/fails_to_deliver` | Existing table is enough once linked to symbol/listing/security. |
| Reg SHO threshold | `market_reg_sho_threshold_v1` | Existing | Whether a ticker is on a Reg SHO threshold list for a date. | Reg SHO threshold publications | `short_pressure/reg_sho` | Existing table is enough. |
| Borrow | `market_security_borrow_v1` or future `security_borrow_fact_v1` | Existing partial | Broker-specific shortability, borrow shares, lender count, and fee/rate. | IBKR borrow/shortability | `borrow_pressure/borrow_availability` | Reuse existing table if it remains broker point-in-time. Do not blend it with FINRA short data. |
| Splits | `market_stock_split_v1` | Existing | Forward/reverse split terms and execution date. | Massive corporate actions, SEC evidence when available | `corporate_action/split_dividend` | Existing table is enough. |
| Dividends | `market_cash_dividend_v1` | Existing | Cash dividend dates and amount. | Massive corporate actions | `corporate_action/split_dividend` | Existing table is enough. |
| IPO terms | `market_ipo_v1` | Existing partial | Listing/IPO timing, issue price, offered shares, status. | Massive IPO, SEC S-1/F-1/424B | `ipo_pipeline/ipo_terms` | Reuse existing table, but add SEC-derived rows only as compact terms, not filing text. |
| Country | `market_security_country_v1` | Existing | Best assertion of listing, issuer legal, HQ, issue, and effective country. | SEC, Massive, exchange/listing data | `reference_identity/country` | Existing table is enough. |
| Classification | `market_security_classification_v1` | Existing | Sector, industry, SIC, exchange classification, or other controlled taxonomy labels. | SEC SIC, Massive overview, exchange metadata | `reference_identity/classification` | Existing table is enough if scheme/source/level are preserved. |
| News catalyst | `security_news_catalyst_fact_v1` | Schema implemented; filler pending | Compact news-derived event labels tied to tickers/securities, such as offering headline, analyst action, FDA event, M&A, litigation, or macro-sensitive news. | Benzinga normalized news, future LLM classifier | `news_catalyst/*` | New table is justified because normalized news is article-centric, while trading needs security-centric labels. |
| SEC filing event | `security_sec_filing_event_fact_v1` | Schema implemented; filler pending | Compact security-linked filing event: form, accepted time, filing status, report date, and mapped entity. | `sec_filing_v2`, SEC-market bridge | `sec_filing/sec_form_type` | New table is justified if `feature_sec_event_market_bridge_v1` remains a feature bridge rather than canonical history. |
| SEC text signal | `security_sec_text_signal_fact_v1` | Schema implemented; filler pending | Extracted labels from filing text, such as offering, ATM, shelf, warrant, going concern, auditor change, delisting, reverse split mention, or risk flags. | `sec_filing_text_v2`, deterministic rules, future LLM extraction | `sec_filing/offering_supply`, `sec_filing/insider_activity`, `data_quality/text_signal` | New table is justified because source text is large and unstructured. Store labels/spans/evidence refs, not full text. |
| Fundamental metric | `issuer_fundamental_metric_fact_v1` | Schema implemented; filler pending | Normalized XBRL metrics such as revenue, net income, assets, liabilities, cash, debt, operating cash flow, EPS, and shares. | `sec_xbrl_company_fact_v1`, `sec_xbrl_frame_observation_v1` | `fundamental/xbrl_fact_change` | New table is justified only for curated metrics used by models/trading. Do not mirror all XBRL facts. |
| Valuation | `security_valuation_fact_v1` | Schema implemented; filler pending | Derived ratios such as market cap to sales, EV-like approximations, price to book, or cash per share when inputs exist. | market snapshot plus curated fundamental metrics | `fundamental/valuation`, `feature_invalidation/valuation` | New table is justified for derived values with explicit input version/evidence. |
| QMD macro bars and market status | not a reference fact table | Out of reference gateway scope | QMD should create macro bars and persist market status. Reference gateway should not own a liquidity/profile fact table unless we later define a compact non-redundant reference publication contract. | QMD | `market_structure/*` | Deferred outside reference gateway fact ownership. |

### QMD Live Tradability Overlay Boundary

Reference tradability and broker routing facts are not the full live order
answer. They cover slow-changing identity, mapping, route, and reference
publication risks. QMD owns the live market-data overlay because it sees the
quote/trade stream first.

QMD now publishes sparse abnormal state transitions to:

```text
q_live.live_symbol_market_event_v1
```

Normal state is not persisted. QMD keeps the active state map in memory and
only appends rows when predefined abnormal states open or close:

- estimated LULD near/breach states from closed bars
- locked/crossed quote states from closed bars
- configured quote/trade halt-resume condition transitions

The order/scanner gate should evaluate:

```text
reference_tradable AND routing_valid AND no active QMD live blocking state
```

Fact-table work in the reference gateway must not duplicate QMD state rows.
The current live block should come from QMD's live-state snapshot or stream.
The trading app/order layer combines QMD live state with reference facts.
Repeated observations of an already-active abnormal state refresh QMD memory
but must not be copied into durable reference facts.

Reference fact-table follow-up tasks:

1. Keep `security_tradability_fact_v1` reference-only: identity, mapping,
   listing, conid, source-conflict, and active-status issues.
2. Keep `security_routing_fact_v1` broker/reference-only.
3. The live trading app must read QMD active abnormal states separately and
   block if any active row has `is_live_tradability_blocking = 1`.
4. Add a reconciliation/audit query that verifies QMD abnormal events can be
   joined to known active symbols, but do not block QMD if a brand-new ticker is
   not yet in reference tables.

### SEC/XBRL Useful Fact Scope

SEC and XBRL should not be copied wholesale into new tables. The first curated
XBRL scope should focus on metrics that affect trading context, supply, and
model features.

Share and supply tags:

- `EntityCommonStockSharesOutstanding`
- `CommonStocksIncludingAdditionalPaidInCapital`
- `CommonStockSharesAuthorized`
- `CommonStockSharesIssued`
- `CommonStockSharesOutstanding`
- `WeightedAverageNumberOfSharesOutstandingBasic`
- `WeightedAverageNumberOfDilutedSharesOutstanding`
- `CommonStockSharesReservedForFutureIssuance`
- warrant, option, convertible, and preferred-share tags when available

Balance sheet and liquidity tags:

- cash and cash equivalents
- total assets
- total liabilities
- current assets
- current liabilities
- debt and notes payable
- stockholders equity

Income/cash-flow tags:

- revenue
- operating income
- net income
- operating cash flow
- free-cash-flow inputs when available
- EPS basic and diluted

These should become rows in `issuer_fundamental_metric_fact_v1` only when they
are part of a curated metric catalog. The full SEC XBRL tables remain the source
of truth for detailed research.

### Text-Derived Fact Scope

Filing and news text is valuable, but it is large and not deterministic enough
to store repeatedly. Text-derived facts should store compact extraction output:

```text
signal_type
signal_subtype
direction
confidence_score
evidence_document_id or canonical_news_id
evidence_span_start/end when available
effective_at_utc
source_content_sha256
```

Useful SEC text signal groups:

- offering or shelf registration
- ATM program
- registered direct or PIPE
- warrant/conversion/dilution terms
- reverse split proposal or execution
- delisting/non-compliance warning
- going concern
- auditor resignation/change
- restatement/amendment
- buyback authorization
- insider ownership or control event

Useful news signal groups:

- offering/capital raise
- analyst action
- FDA/clinical/regulatory event
- merger/acquisition/strategic review
- earnings/guidance
- litigation/investigation
- contract/customer/order
- macro/geopolitical event tied to listed securities

### Alert-To-Fact Filler Flow

Alerts should drive fact updates instead of repeatedly scanning every source
table.

Example flow:

```text
new SEC filing row
-> alert: sec_filing/sec_form_type
-> SEC filing event filler updates security_sec_filing_event_fact_v1
-> if form or text implies supply risk, text/supply filler updates security_sec_text_signal_fact_v1 or security_share_supply_fact_v1
-> alert: feature_invalidation/scanner_static
-> publication builder refreshes affected ticker rows
```

For source updates that arrive in batches, the filler should process affected
entities from alert keys, not recompute the full market unless the alert says
the publication coverage changed globally.

### Flow Position

The fact layer sits between alert emission and trading publications:

```text
source services normalize data
-> reference gateway audits/source-syncs
-> reference gateway emits alerts
-> fact fillers update compact canonical fact tables
-> publication builders refresh latest trading tables
-> scanner/live trading/model/review consumers read publications and alerts
```

Fact fillers are internal reference-gateway work. External services may consume
the finished facts, publications, and alerts, but they should not implement
their own competing source-to-fact logic.

### Initial Fill Design

Initial fill is different from normal daemon operation. During normal operation,
alerts tell the reference gateway which source row or entity changed. During
initial fill, historical alerts may not exist for all old source rows, so the
initial fill must scan source tables once and build the baseline fact history.

Proposed entry point:

```text
pipelines/reference_data/facts/reference_fact_initial_fill.py
```

The script should support:

```text
--read-database q_live
--write-database q_reference_tmp
--execute
--from-date YYYY-MM-DD
--to-date YYYY-MM-DD
--families deterministic|llm|all
--workers N
--batch-rows N
```

Run order:

1. Run against `q_reference_tmp`.
2. Audit fact counts, source coverage, duplicate keys, and orphan entity links.
3. Compare latest publications built from temp facts against current `q_live`
   publications where possible.
4. Run against `q_live` only after temp audit is accepted.

The initial fill must write manifests and coverage rows so the service knows
which fact families have a trusted baseline. After that, daemon runs should use
alerts and incremental source windows rather than rescanning all history.

### Initial Fill Phases

Phase 0: source validation.

Purpose:

- prove the source tables needed by the selected fact families exist
- prove required identity bridges are populated
- prevent silent partial fills

Required checks:

- `id_issuer_v1`, `id_security_v1`, `id_listing_v1`, and `id_symbol_v1` exist
- `id_sec_market_bridge_v1` exists for SEC-linked facts
- source tables have date ranges compatible with the requested fill window
- every written fact can carry a source reference

Phase 1: deterministic identity and guardrail facts.

Outputs:

- `security_tradability_fact_v1`
- `security_routing_fact_v1`

Inputs:

- canonical identity tables
- latest/open `id_mapping_issue_v1`
- `feature_tradable_universe_v1`
- IBKR conid evidence already stored in listing/source-mapping tables

This phase can be done with smart ClickHouse queries. It should not require LLM
or external provider calls.

Phase 2: deterministic SEC/XBRL and market-publication facts.

Outputs:

- `security_sec_filing_event_fact_v1`
- `issuer_fundamental_metric_fact_v1`
- `security_share_supply_fact_v1`
- existing publication tables such as short volume, short interest, FTD, Reg
  SHO, borrow, snapshot, split, dividend, IPO, country, and classification

Inputs:

- `sec_filing_v2`
- `sec_xbrl_company_fact_v1`
- `sec_xbrl_frame_observation_v1`
- `id_sec_market_bridge_v1`
- current `market_*` publication tables

This phase should be SQL-first. It uses curated XBRL tag catalogs and as-of
logic. It must not mirror all XBRL rows into the curated fact table; only metrics
selected for trading/model use belong in `issuer_fundamental_metric_fact_v1`.

Phase 3: deterministic text/news rule facts.

Outputs:

- initial rows in `security_sec_text_signal_fact_v1`
- initial rows in `security_news_catalyst_fact_v1`

Inputs:

- `sec_filing_text_v2`
- `benzinga_news_normalized_v1`
- `benzinga_news_ticker_v1`

This phase can use deterministic rules, keyword dictionaries, regexes, and
already-existing labels. It can run in ClickHouse plus Python batching, but it
should still avoid LLM calls. Examples: form-type labels, explicit offering
phrases, reverse-split mentions, going-concern phrases, and headline/source
labels that are already present.

Phase 4: workstation/model extraction.

Outputs:

- higher-confidence or richer rows in `security_sec_text_signal_fact_v1`
- higher-confidence or richer rows in `security_news_catalyst_fact_v1`

Inputs:

- source text rows selected by Phase 3 as needing model extraction
- model prompts and model-output contracts

This phase is separate because it can be expensive and should be batched on the
workstation. It may use local vLLM/OpenAI-compatible endpoints, but it must write
the same compact fact schema as deterministic extraction. It should never write
raw prompts, full source text, or full model traces into fact tables. Store those
only in controlled artifacts/logs if needed, with source hashes in the fact row.

Phase 5: derived facts.

Outputs:

- `security_valuation_fact_v1`

Inputs:

- `issuer_fundamental_metric_fact_v1`
- `market_security_market_snapshot_v1`
- QMD/historical SIP publications

Valuation can be reference-gateway-owned once curated fundamentals and snapshots
exist. Liquidity should be QMD-owned because it is computed from market events
and bars; reference gateway should consume or publish the compact output, not
recompute raw market microstructure.

Phase 6: latest trading publications.

Outputs:

- `feature_tradable_universe_v1`
- `feature_scanner_static_v1`
- future `security_trading_context_latest_v1`

Inputs:

- canonical graph
- canonical fact tables
- existing market publication tables

This phase is what live trading and scanner should read. It is intentionally
redundant only in the useful direction: it stores latest compact fields so the
trading UI does not perform heavy joins while the market is open.

Phase 7: audit and coverage close.

Required audits:

- every fact row has source-system, source-table, and source-event reference
- every security-linked fact maps to the current canonical graph
- duplicate fact IDs are zero
- table-specific duplicate semantic keys are zero or intentionally replaced
- source rows skipped by deterministic filters are counted with reason
- latest trading publications match latest facts for sampled tickers
- temp-database output can be recreated with the same parameters

Only after these audits pass should the fill coverage be marked as completed.

### Deterministic Versus LLM Work

The initial fill must keep deterministic SQL/table-only work separate from
LLM/model work.

Deterministic work:

- can run directly from existing `q_live` tables
- can be repeated safely
- should run first
- should produce the minimum useful baseline
- should be usable in `q_reference_tmp` without workstation model dependencies

LLM/model work:

- should be optional and separately resumable
- should run in batches on the workstation
- should read a queue of source rows selected by deterministic rules or missing
  labels
- should write only compact fact rows and model metadata, not raw text copies
- should be auditable against deterministic labels where possible

If a fact family can be filled by SQL, it should not wait for LLM extraction.
For example, SEC filing events, XBRL curated metrics, share-supply XBRL facts,
short volume, short interest, and FTD are DB-only. SEC text signal enrichment
and nuanced news-catalyst classification can have a second LLM phase.

### Stage 7 Rules

- Fact tables must align with alert families/groups.
- A fact table must have a clear owner, source tables, entity key, time key, and
  update trigger.
- Existing `market_*` publication/fact tables should be reused when their
  semantics are already correct.
- New tables are allowed only when they remove ambiguity or prevent expensive
  repeated joins/parsing.
- Initial fill must run deterministic DB-only phases before any LLM/model phase.
- LLM/model phases must be optional, batched, resumable, and written to the same
  compact fact schemas.
- The scanner/live-trading app should read latest publication tables, not run
  wide joins over source/fact tables during trading.
- Backtests and model building should use fact tables with as-of joins.
- Full provider payloads stay in source/artifact tables, not facts.

Review status: under review.

## Public Operator Knobs

The wrapper exposes only high-level behavior groups.

| Knob | Values | Default | Meaning |
| --- | --- | --- | --- |
| `-Mode` | `Prod`, `Temp` | `Prod` | `Prod` reads/writes `q_live`. `Temp` reads `q_live` and writes `q_reference_tmp`. |
| `-Run` | `Daemon`, `Once` | `Prod=Daemon`, `Temp=Once` | Controls process lifetime. |
| `-Integrity` | `Strict`, `ReportOnly` | `Strict` | `Strict` writes issue rows, resolves deterministic issues, and blocks tradability. `ReportOnly` audits without guardrail writes. |
| `-Maintenance` | `Auto`, `Skip`, `Force` | `Auto` | `Auto` runs heavy maintenance only when policy allows. `Skip` disables heavy maintenance. `Force` allows maintenance with a required reason. |
| `-MaintenanceReason` | text | empty | Required with `-Maintenance Force` in production. |
| `-Diagnostics` | `None`, `Rules`, `TableGroups`, `Config` | `None` | Prints a diagnostic view and exits without operational writes. |

Normal production:

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

Forced maintenance:

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once -Maintenance Force -MaintenanceReason "reviewed after-hours repair"
```

Diagnostics:

```powershell
.\scripts\run_reference_gateway.ps1 -Diagnostics Rules
.\scripts\run_reference_gateway.ps1 -Diagnostics TableGroups
.\scripts\run_reference_gateway.ps1 -Diagnostics Config
```

## Objectives

| Objective | What it does | Market-hours behavior | After-hours behavior |
| --- | --- | --- | --- |
| Source sync | Fetch current evidence from Massive, IBKR, FINRA, SEC, and related providers. | Runs normally. It may write compact evidence and issues. | Runs normally. |
| Integrity guardrail | Audit reference data, resolve deterministic issues, and block unsafe instruments. | Runs normally. Risk-reducing issue writes and `is_tradable=0` blocks are allowed. | Runs normally. |
| Maintenance | Schema upkeep, canonical graph promotion, full publication rebuilds, and historical publication gap fill. | Deferred in `Auto`; allowed only with `Force` and a reason. | Allowed in `Auto`. |
| Observability | Preflight, runtime JSONL logs, reports, and terminal output. | Always enabled for operational runs. | Always enabled for operational runs. |

## Startup Flow

1. The PowerShell wrapper builds:

   ```powershell
   python -m services.reference_gateway.main --mode prod --run daemon --integrity strict --maintenance auto --diagnostics none
   ```

2. Python loads `.env` files.

3. CLI values are passed to `ReferenceGatewayConfigOverrides`.

4. `ReferenceGatewayConfig.from_env(...)` merges env defaults and CLI
   overrides into one immutable config object.

5. Diagnostics modes exit before dependency checks or writes.

6. Operational modes run preflight. ClickHouse, artifact storage, Massive, and
   IBKR Client Portal must be available for source sync.

7. Daemon mode starts a parent loop. Each cycle launches the same gateway with
   `--run once`; the child uses the same high-level knobs and the same write
   policy.

8. One-shot mode performs:

   - dependency preflight
   - safe schema upkeep if maintenance policy allows it
   - deterministic issue resolution
   - optional publication rebuild if policy allows it
   - audit
   - source sync
   - issue writes and immediate tradability blocking in strict mode
   - canonical graph promotion if maintenance policy allows it
   - recent market-publication gap fill if maintenance policy allows it
   - final report and runtime log events

## Config Groups

| Group | What it controls |
| --- | --- |
| `service` | Operator mode, run mode, bind, host, port, storage roots, report root. |
| `database` | ClickHouse URL/user, read DB, write DB, temp-write detection. |
| `providers` | Massive endpoint/key presence and IBKR Client Portal endpoint. |
| `execution` | Execute mode, daemon mode, diagnostics mode, daemon intervals, preflight. |
| `source_sync` | Page limits and new-candidate caps. Source sync itself is not optional in operational runs. |
| `integrity` | Strict versus report-only issue handling and immediate tradability blocking. |
| `maintenance` | Canonical graph writes, publication rebuilds, schema upkeep, and publication gap fill. |
| `terminal` | Rich terminal display settings. |

## Removed Public Controls

These are intentionally no longer public operator knobs:

| Removed control | Replacement |
| --- | --- |
| `-ActiveTickerCheck` | Source sync always runs in operational modes. |
| `-Execute` / `-NoExecute` | `-Diagnostics` controls report-only diagnostics; operational modes execute. |
| `-ReadDatabase`, `-WriteDatabase`, `-TestWriteDatabase` | `-Mode Prod` and `-Mode Temp`. Env overrides remain for deployment-specific defaults. |
| `-NoPreflight` | Operational modes require preflight. |
| `-NoImmediateTradabilityBlock` | Use `-Integrity ReportOnly` only for diagnostics. |
| `-NoWriteCanonicalGraph`, `-NoRebuildTradable`, `-NoMarketPublicationGapFill` | `-Maintenance Skip` disables maintenance; `Auto` uses market-hours policy. |
| `-EnsureMarketPublicationSchema` | Schema upkeep is maintenance. |
| `-MarketHoursWriteOverride` | `-Maintenance Force -MaintenanceReason "..."`. |

## Current Review Read

The public contract now matches the service objectives:

- `Mode` chooses production versus temp safety.
- `Run` chooses daemon versus one-shot.
- `Integrity` chooses whether guardrails write or only report.
- `Maintenance` chooses whether heavy/promotion work is allowed.
- `Diagnostics` prints read-only explanatory output.

The detailed implementation controls remain internal fields on
`ReferenceGatewayConfig`, but the operator no longer has to assemble them by
hand.

## Validation Notes: Runtime Behavior

Goal: prove the simplified public controls behave correctly in real gateway
execution before adding more functionality.

Run these in order.

### 1. Temp One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp
```

Expected behavior:

- reads from `q_live`
- writes only to `q_reference_tmp`
- runs preflight
- runs source sync
- runs reference audit
- writes temp reports/logs
- does not mutate `q_live`

Pass condition:

- no `q_live` writes are observed
- temp report clearly shows `read_database=q_live`,
  `write_database=q_reference_tmp`, and `test_write_mode=true`

### 2. Temp Force Maintenance

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Temp -Maintenance Force
```

Expected behavior:

- reads from `q_live`
- writes to `q_reference_tmp`
- allows schema/maintenance paths in the temp database
- uses the wrapper's default temp maintenance reason
- does not mutate `q_live`

Pass condition:

- temp schema/maintenance paths complete or fail with an actionable temp-DB
  missing-table reason
- no production table is changed

### 3. Production One-Shot

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod -Run Once
```

Expected behavior:

- reads and writes `q_live`
- runs strict integrity guardrails
- writes issues and immediate non-tradable blocks if needed
- defers maintenance if market-hours policy blocks it

Pass condition:

- any blocked maintenance is reported as policy-blocked, not silently skipped
- any discovered blocking issue makes affected rows non-tradable

### 4. Production Daemon

```powershell
.\scripts\run_reference_gateway.ps1 -Mode Prod
```

Expected behavior:

- parent daemon performs startup preflight
- each child cycle runs with `--run once`
- runtime JSONL logs are written
- a child failure stops the daemon instead of being hidden

Pass condition:

- daemon logs show parent start, child cycle command, child result, and next
  interval
- stopping/restarting is clear and does not leave an orphaned child process

## Implemented Notes: Runtime Status And Terminal UX

Status: implemented.

The reference gateway now uses a live Rich terminal session during one-shot
child cycles when Rich output is enabled. The terminal updates after each major
operation and keeps a stable panel structure:

- header with UTC, ET, Vancouver time, mode, read/write DBs, data root, and
  report path
- current operation
- dependency status
- runtime summary
- source-sync counters
- integrity guardrail status
- maintenance policy/state
- full operation log
- prioritized audit findings

The compact layout removes lower-priority panels when the terminal height is
short, keeping the current operation, summary, maintenance, and audit findings
visible.

Structured JSONL events were added for audit summaries and source-sync
summaries. Existing `operation` events continue to back the per-step terminal
rows.

Validation performed:

- `python -m py_compile services\reference_gateway\terminal.py services\reference_gateway\main.py`
- `python -m services.reference_gateway.main --help`
- `python -m services.reference_gateway.smoke_test`
- normal-height terminal render smoke
- compact-height terminal render smoke
