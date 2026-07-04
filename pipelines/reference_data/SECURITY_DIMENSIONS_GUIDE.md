# Security Dimensions Query Layer

This module gives the trading app a deterministic way to ask: "what do we know about this security?" without creating a redundant fact table for every possible field.

The implementation lives in:

- `pipelines/reference_data/security_dimensions.py`

It reads from existing q_live source tables and returns normalized dimension rows. It does not write data and it does not decide trading permissions by itself.

## Why This Exists

The reference database already contains the source data: SEC XBRL facts, filings, Massive market publications, FINRA short volume, IBKR borrow data, country/classification assertions, news metadata, and reference-gateway tradability/routing facts.

The trading app needs two access patterns:

1. Single security: fetch historical observations for one ticker and plot or inspect the progression through time.
2. Scanner: fetch the latest/as-of values for many tickers and a small dimension set efficiently.

The dimension layer solves this by building source-backed ClickHouse SQL. The app can choose the dimensions it needs and avoid scanning unrelated source tables.

## Output Contract

Every query returns rows with this shape:

| Column | Meaning |
| --- | --- |
| `symbol_id` | Canonical symbol id from the reference graph. |
| `ticker` | Uppercase ticker used by the trading app. |
| `dimension_code` | Stable machine code, for example `short_volume_ratio`. |
| `dimension_label` | Human label for display. |
| `dimension_group` | Functional group, for example `short_pressure` or `share_supply`. |
| `observed_at_utc` | Time when the value became available to us or the provider observation time. |
| `period_end_date` | Economic/reporting period date when available. |
| `value` | Numeric value, nullable. |
| `value_text` | Text value for categorical dimensions. |
| `value_bool` | Boolean value as nullable `UInt8`. |
| `value_unit` | Unit such as `shares`, `USD`, `ratio`, `status`, or `bool`. |
| `source_system` | Provider or service: `sec`, `massive`, `finra`, `ibkr`, `benzinga`, `reference_gateway`. |
| `source_table` | Source table used for the dimension. |
| `source_event_id` | Accession number, source reference, canonical news id, or similar source key. |
| `source_form` | SEC form, dividend type, IPO status, or another event subtype when available. |
| `source_priority` | Lower means more authoritative for tie-breaking inside a dimension. |

## Registry

Use `dimension_registry()` to inspect every available dimension:

```python
from pipelines.reference_data.security_dimensions import dimension_registry

registry = dimension_registry()
for code, dimension in registry.items():
    print(code, dimension.group, dimension.value_type, dimension.source_table)
```

Useful helpers:

```python
from pipelines.reference_data.security_dimensions import (
    all_dimension_codes,
    default_dimension_codes,
    dimension_codes_for_groups,
    scanner_default_dimension_codes,
)

all_codes = all_dimension_codes()
plot_codes = default_dimension_codes()
scanner_codes = scanner_default_dimension_codes()
short_codes = dimension_codes_for_groups(["short_pressure", "borrow"])
```

## One Ticker Flow

Use this for a security profile, detail pane, or chart overlay.

```python
import json

from pipelines.reference_data.security_dimensions import (
    resolve_security_dimension_context_sql,
    security_dimension_observations_sql_for_context,
    SecurityDimensionContext,
)

raw = client.execute(resolve_security_dimension_context_sql(database="q_live", ticker="AAPL"))
row = json.loads(raw.splitlines()[0])
context = SecurityDimensionContext(
    ticker=row["ticker"],
    cik=row["cik"],
    symbol_id=row["symbol_id"],
    listing_id=row["listing_id"],
    security_id=row["security_id"],
    issuer_id=row["issuer_id"],
)

sql = security_dimension_observations_sql_for_context(
    database="q_live",
    context=context,
    dimension_codes=(
        "sec_entity_common_stock_shares_outstanding",
        "sec_entity_public_float",
        "short_volume_ratio",
        "borrow_fee_rate",
    ),
    start_date="2024-01-01",
    end_date="2027-01-01",
)
rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
```

For interactive pages, resolve the context once and cache it in memory. The context contains the ids needed to join SEC, Massive, FINRA, IBKR, and reference-gateway tables without repeatedly resolving the same ticker.

## Scanner Flow

Use the latest/as-of query for many tickers. It returns one row per ticker per dimension.

`scanner_default_dimension_codes()` is intentionally a fast operational set. It avoids the heavy SEC/XBRL and SEC-text dimensions unless the app explicitly requests them.

```python
from pipelines.reference_data.security_dimensions import (
    scanner_default_dimension_codes,
    security_dimension_latest_sql_for_tickers,
)

sql = security_dimension_latest_sql_for_tickers(
    database="q_live",
    tickers=("AAPL", "MSFT", "NVDA"),
    dimension_codes=scanner_default_dimension_codes(),
    as_of="2027-01-01 00:00:00",
)
rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
```

The trading app should pivot this result by `(ticker, dimension_code)` when it needs a wide scanner table.

## Efficient Fetch Rules

- Request only the dimensions needed for the page.
- For scanner tables, use `security_dimension_latest_sql_for_tickers()`, not full history.
- Use `scanner_default_dimension_codes()` for live scanner screens.
- Request SEC/XBRL fundamentals by explicit code or group on detail pages, or in a slower background scanner enrichment path.
- Batch tickers. A scanner page should call one multi-ticker query, not one query per ticker.
- Cache resolved contexts while the app session is open.
- For very large scanner universes, page by ticker list or by the app's current scanner candidate list.
- Avoid text-heavy dimensions unless the UI displays them.

## Dimension Groups

| Group | Examples | Use |
| --- | --- | --- |
| `share_supply` | SEC shares outstanding, Massive shares outstanding | Float/share evolution and dilution awareness. |
| `float` | SEC public float, Massive free float | Liquidity and squeeze context. |
| `short_pressure` | Short interest, short volume, Reg SHO threshold | Short crowding and pressure context. |
| `borrow` | IBKR borrow status, shortable shares, fee rate | Practical short availability and cost. |
| `fails_to_deliver` | FTD quantity and previous close | Settlement stress context. |
| `corporate_action` | Splits, dividends, IPO values | Event and chart adjustment context. |
| `sec_filing` | Filing size, text chars, document count | Filing activity and text-analysis availability. |
| `fundamentals` | Revenue, gross profit, income, EPS | Deterministic XBRL fundamentals. |
| `balance_sheet` | Assets, liabilities, equity, debt | Financial position. |
| `cash_flow` | Operating cash flow, capex | Cash generation and reinvestment. |
| `capital_return` | Dividends and repurchases | Buyback/dividend context. |
| `classification` | Country and classification values | Jurisdiction and category context. |
| `news` | News count, provider delay, title-only/pdf flags | News availability and quality context. |
| `tradability` | Reference-gateway tradability fact | Reference integrity hard gate. |
| `routing` | IBKR conid | Order-routing identity. |

## Important Semantics

`observed_at_utc` is the time the data was available or observed by the provider. For SEC filings this is usually `accepted_at_utc`; for daily source files it can be a date converted to midnight. `period_end_date` is the financial or source period. Use `observed_at_utc` for market reaction alignment.

SEC XBRL values are raw reported values. They are not split-adjusted. This is intentional: the source-backed layer should preserve source meaning. Chart code can optionally display split-adjusted views later.

Text values and booleans are returned in `value_text` and `value_bool`. Do not assume every dimension has `value`.

## Experiment Script

To inspect one ticker:

```powershell
python pipelines\reference_data\experiments\plot_security_dimensions.py --ticker AAPL --start-date 2019-01-01 --end-date 2027-01-01
```

To request specific dimensions:

```powershell
python pipelines\reference_data\experiments\plot_security_dimensions.py --ticker NVDA --dimensions sec_entity_common_stock_shares_outstanding,massive_share_class_shares_outstanding,short_volume_ratio --start-date 2023-01-01 --end-date 2027-01-01
```

The plotter skips non-numeric rows when rendering but still writes the full JSONL result and query SQL for inspection.

## What This Does Not Do

- It does not persist a new fact table.
- It does not mutate tradability.
- It does not enrich missing source data.
- It does not replace source-sync, maintenance, or issue-resolution logic in the reference gateway.
- It does not perform LLM extraction from SEC text. LLM-derived dimensions should use a separate source table and can be added to this registry later.
