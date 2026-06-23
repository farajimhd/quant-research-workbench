# Reference Gateway

The reference gateway owns the slow-changing market identity graph:

```text
issuer -> security -> listing -> symbol
```

It is separate from QMD, news, and SEC. QMD streams quotes/trades/bars, news
streams Benzinga articles, and SEC streams filings/XBRL. This service maintains
the source mappings that make those streams tradable and joinable.

## Hard Tradability Rule

Any issue means the security is not tradable.

The service must never guess an orderable instrument. A row can enter
`feature_tradable_universe_v1` as `is_tradable = 1` only when all required
relationships are resolved and unambiguous:

- active source symbol
- active listing
- active security
- supported US stock/common-stock product type
- USD listing currency
- US exchange
- valid positive IBKR conid
- no open mapping issue touching the source symbol/listing/security
- no ambiguous IBKR contract match
- no unresolved Massive/IBKR exchange mapping

If any of those checks fails, the row remains present for review but must be
published as `is_tradable = 0` with an `exclusion_reason`.

The current publisher enforces this rule directly in
`feature_tradable_universe_v1`. Rows with weak issuer identity, duplicate CIK/
LEI/EIN ownership, non-US exchange country, unsupported product type, invalid
IBKR conid, or a directly linkable open mapping issue are blocked before they
can enter scanner/live-trading setup.

## Ticker And Conid Sync Design

Massive active tickers are source observations, not tradable instructions. A
Massive ticker is resolved into the canonical graph in this order:

1. identify or create an issuer using durable evidence such as CIK when present
2. identify or create a security using FIGI/share-class evidence when present
3. identify or create a listing using security, exchange, and currency
4. attach the source ticker to the listing as an `id_symbol_v1` row
5. record evidence in `id_source_mapping_v1`
6. record ambiguity or missing evidence in `id_mapping_issue_v1`

IBKR conid resolution only runs after a candidate listing exists. The resolver
must filter IBKR search results to exact US stock/USD candidates and accept a
conid only when there is exactly one unambiguous contract for the listing. If
IBKR returns several plausible contracts, the listing is non-tradable until a
human or a stronger resolver settles the mapping.

Exchange codes are maintained through an alias layer. Massive and IBKR exchange
codes should map to one canonical `ref_exchange_v1.exchange_code`; a new or
unmapped exchange opens an issue and blocks tradability.

## Current Executable Step

The default step is an audit/planner. Without `--execute`, it does not mutate
identity tables. It checks the current `q_live` reference graph and writes a
JSON report.

```powershell
python -m services.reference_gateway.main
```

Reference writes can be tested against a temporary database while reads still
come from `q_live`, matching the SEC gateway test pattern:

```powershell
python -m services.reference_gateway.main --read-database q_live --test-write-database q_reference_tmp --execute --ensure-market-publication-schema
```

The same mode through the wrapper:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -TestWriteDatabase q_reference_tmp -Execute -EnsureMarketPublicationSchema
```

During the active collection window, writes are blocked unless the operation is
explicitly marked with an auditable override reason. For a temp-mode smoke test:

```powershell
.\scripts\run_reference_gateway.ps1 -ReadDatabase q_live -TestWriteDatabase q_reference_tmp -Execute -EnsureMarketPublicationSchema -MarketHoursWriteOverride -MarketHoursWriteReason "temp reference gateway test"
```

Equivalent environment variables:

```text
REFERENCE_CLICKHOUSE_READ_DATABASE=q_live
REFERENCE_CLICKHOUSE_WRITE_DATABASE=q_reference_tmp
```

Disable the test by removing the write override or setting
`REFERENCE_CLICKHOUSE_WRITE_DATABASE=q_live`.

To print the blocking rules:

```powershell
python -m services.reference_gateway.main --print-rules
```

To print the table ownership groups:

```powershell
python -m services.reference_gateway.main --print-table-groups
```

To run the market-open ticker reconciliation once:

```powershell
python -m services.reference_gateway.main --active-ticker-check
```

That pass fetches Massive active US stock tickers, compares them against
`id_symbol_v1`/`id_listing_v1`, fetches compact Massive overview evidence for
new tickers, and optionally queries IBKR Client Portal when
`REFERENCE_GATEWAY_IBKR_RESOLUTION_ENABLED=true`.

Without `--execute`, this is still report-only. With `--execute`, discovered
open mapping issues are inserted into `id_mapping_issue_v1` by default. Those
issue rows are the source-of-truth blocker; the audit is not allowed to patch
`is_tradable` directly.

In execute mode the gateway also rebuilds `feature_tradable_universe_v1` and
`feature_scanner_static_v1` before audit, using the existing step 6 publisher.
If active-ticker reconciliation writes new open issues, it rebuilds the feature
publications again so the latest tradable universe reflects those new blockers.

The safety flow is:

1. discover provider/reference issue
2. write canonical open row in `id_mapping_issue_v1`
3. insert clean new candidates into the canonical graph only when CIK, FIGI,
   exchange, currency, ticker, and one compatible IBKR conid are unambiguous
4. close stale reference-gateway issues when the canonical symbol is now valid
5. rebuild `feature_tradable_universe_v1`
6. publish affected rows as `is_tradable = 0` unless every hard rule passes
7. audit validates that no tradable row violates hard rules

The canonical graph writer is intentionally conservative. It writes new rows to
`id_issuer_v1`, `id_issuer_identifier_v1`, `id_security_v1`,
`id_security_identifier_v1`, `id_listing_v1`, `id_symbol_v1`, and
`id_source_mapping_v1` only for candidates that are already clean. If any
required evidence is missing or conflicting, the candidate becomes an open issue
instead of a guessed tradable listing.

Useful controls:

```text
REFERENCE_GATEWAY_ACTIVE_TICKER_CHECK_ENABLED=false
REFERENCE_GATEWAY_ACTIVE_TICKER_CHECK_MARKET_HOURS_ONLY=true
REFERENCE_GATEWAY_ACTIVE_TICKER_PAGE_LIMIT=1000
REFERENCE_GATEWAY_ACTIVE_TICKER_MAX_PAGES=1000
REFERENCE_GATEWAY_ACTIVE_TICKER_NEW_CANDIDATE_LIMIT=250
REFERENCE_GATEWAY_IBKR_RESOLUTION_ENABLED=false
REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES=true
REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH=true
REFERENCE_GATEWAY_RESOLVE_STALE_ISSUES=true
REFERENCE_GATEWAY_REBUILD_TRADABLE_ON_EXECUTE=true
REFERENCE_GATEWAY_REBUILD_TRADABLE_IN_TEST_MODE=false
REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_ENABLED=true
REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_DAYS=14
```

`REFERENCE_GATEWAY_REBUILD_TRADABLE_IN_TEST_MODE=false` is intentional. A temp
write database usually does not contain the full identity graph required by the
step 6 publisher. Set it to `true` only after cloning the required q_live
identity/source tables into the temp database.

Equivalent one-off CLI switches:

```powershell
python -m services.reference_gateway.main --execute --no-write-discovered-issues
python -m services.reference_gateway.main --execute --no-write-canonical-graph
python -m services.reference_gateway.main --execute --no-resolve-stale-issues
python -m services.reference_gateway.main --execute --no-rebuild-tradable
python -m services.reference_gateway.main --execute --rebuild-tradable-in-test-mode
python -m services.reference_gateway.main --execute --no-market-publication-gap-fill
```

Wrapper equivalents:

```powershell
.\scripts\run_reference_gateway.ps1 -Execute -NoWriteDiscoveredIssues
.\scripts\run_reference_gateway.ps1 -Execute -NoWriteCanonicalGraph
.\scripts\run_reference_gateway.ps1 -Execute -NoResolveStaleIssues
.\scripts\run_reference_gateway.ps1 -Execute -NoRebuildTradable
.\scripts\run_reference_gateway.ps1 -Execute -RebuildTradableInTestMode
.\scripts\run_reference_gateway.ps1 -Execute -NoMarketPublicationGapFill
```

Enable IBKR resolution only when Client Portal Gateway is authenticated. IBKR
results are compacted to candidate contract fields; the gateway does not persist
raw IBKR payloads.

Reports are written under:

```text
REFERENCE_GATEWAY_REPORT_ROOT_WIN
```

or by default:

```text
<market-data>/prepared/reference_gateway/reports
```

## Scheduling Policy

Read-only audits can run at any time.

Reference-data writes are different. They should normally run after the active
market collection window because they can change the tradable universe,
exchange aliases, issuer mappings, or IBKR conid availability while QMD and the
live trading app are using those rows.

Defaults:

```text
REFERENCE_GATEWAY_AFTER_HOURS_WRITES_ONLY=true
REFERENCE_GATEWAY_COLLECTION_START_ET=04:00
REFERENCE_GATEWAY_COLLECTION_END_ET=20:00
```

If a market-hours operation is truly required, it must be explicit:

```text
REFERENCE_GATEWAY_MARKET_HOURS_WRITE_OVERRIDE=true
REFERENCE_GATEWAY_MARKET_HOURS_WRITE_REASON=<specific reason>
```

The override is intentionally noisy. It is for urgent corrections only, for
example blocking a clearly wrong conid or adding a newly listed security needed
by the current session.

The gateway can also run as a simple daemon:

```powershell
.\scripts\run_reference_gateway.ps1 -Execute -ActiveTickerCheck -Daemon
```

In daemon mode, the process reruns the same one-shot gateway command. During the
active collection window it drops `--execute` unless an explicit market-hours
override is supplied, so active-window cycles are read-only by default. Outside
the active window, execute-mode cycles may write issues, clean graph rows,
stale-issue closures, publication rebuilds, and recent market-publication
coverage fills.

Daemon intervals:

```text
REFERENCE_GATEWAY_DAEMON_ACTIVE_INTERVAL_SECONDS=900
REFERENCE_GATEWAY_DAEMON_AFTER_HOURS_INTERVAL_SECONDS=3600
```

## Issuer Group

All integrated groups are defined in:

```text
services/reference_gateway/TABLE_GROUPS.md
```

Market reference publications are now integrated as group 7. The group includes
existing migrated tables plus new compact publication tables:

```text
market_fails_to_deliver_v1
market_reg_sho_threshold_v1
market_security_borrow_v1
market_security_country_v1
market_reference_publication_coverage_v1
```

Initialize those tables after hours:

```powershell
python -m services.reference_gateway.main --ensure-market-publication-schema
```

Historical publication fill:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

Workstation runtime command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

Temporary write-database fill test:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_reference_tmp --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

The first enabled historical sources are FINRA consolidated NMS daily short-sale
volume and SEC fails-to-deliver. They write coverage rows so later runs resume
from uncovered windows. IBKR borrow availability is point-in-time only; it is
stored as broker-observed availability, not reconstructed historically.

Historical fill dry-runs are read-only. If the write database does not already
have `market_reference_publication_coverage_v1`, the dry-run reports
`schema_missing` instead of creating tables. Run the schema initializer or use
`--execute` when you intend to create/alter the temp database.

Implemented source writers:

```text
finra_short_volume:CNMS
sec_fails_to_deliver
```

The remaining publication source kinds are schema/planning entries until their
specific writers are enabled. Maintenance reports them as
`planned_not_implemented` so they remain visible without failing temp smoke
tests.

The one-shot gateway can launch the recent market-publication coverage fill
after the audit in execute mode. It uses the same
`market_publications_historical_gap_fill.py` script above, defaults to the last
14 days, and respects `market_reference_publication_coverage_v1`.
Temp write-database runs skip this fill unless `--market-publication-gap-fill`
is passed explicitly.

The second table group is issuer identity:

```text
id_issuer_v1
id_issuer_identifier_v1
```

The audit checks this group before any writer is enabled:

- active issuers without CIK, LEI, or EIN
- duplicate durable issuer identifiers across multiple issuers
- securities whose issuer parent is missing
- active trading candidates whose issuer identity is weak

SEC, Massive overview, and IBKR are evidence sources only. They must not write
redundant source blobs into canonical tables. If an existing issuer row is
missing a field and the new value is unambiguous, the future writer can insert a
replacement row with the same issuer id and the missing field filled. If the new
value conflicts with a populated field, the conflict goes to
`id_mapping_issue_v1`; the affected security remains non-tradable.

## Validation

Fast local smoke test:

```powershell
python -m services.reference_gateway.smoke_test
```

This validates the conservative graph-row builder without touching ClickHouse.
For full temp-database validation, run the gateway with a temp write database,
then inspect the generated audit report before enabling writes to `q_live`.
