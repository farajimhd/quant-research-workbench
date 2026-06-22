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

The first executable step is an audit/planner. It does not mutate identity
tables. It checks the current `q_live` reference graph and writes a JSON report.

```powershell
python -m services.reference_gateway.main
```

To print the blocking rules:

```powershell
python -m services.reference_gateway.main --print-rules
```

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

## Next Implementation Stage

After the audit output is reviewed, the writer stage should be added in this
order:

1. Massive active ticker crawler with raw artifact hashes.
2. Exchange alias audit and proposed mappings.
3. Canonical graph resolver in dry-run mode.
4. IBKR missing-conid resolver in dry-run mode.
5. `feature_tradable_universe_v1` publisher that applies the hard tradability
   rule.
6. Only then enable writes for new source mappings and accepted canonical rows.
