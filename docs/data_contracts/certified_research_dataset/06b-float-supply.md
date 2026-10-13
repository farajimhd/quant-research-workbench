# Certified float and share supply

[Fundamental families](06-fundamentals-short-data.md) · [Exact fact schema](12c-schema-facts-state.md)

## Inputs and execution

The runnable `run_float` producer requires certified same-dataset parents for issuer fundamentals, security regular prices, corporate-action coverage and dated issuer-to-security allocation. A certified security-specific reported-supply parent is optional. Parent receipts are independently read back; issuer/security mismatches and changed dependencies block publication.

Shared as-of observations are prepared once in compressed ClickHouse staging. Prices and corporate revisions consume that context inside ClickHouse. No event files or local data exports are used. The staging table uses `live_market_ssd`; it is not published authority.

The regular-price producer derives unadjusted closes from certified 1-second bars. It verifies dated listing/security membership and the exchange calendar, including early closes. PM and AH prices cannot substitute for regular closes. Missing eligible prices produce explicit unavailable records.

## Outputs and qualification

| Output | Meaning |
| --- | --- |
| `float.reported` | Certified security-specific reported float |
| `float.estimated` | Qualified SEC public-float market value / measurement-aligned regular price, adjusted for confirmed executed splits |
| `float.lower_bound` | Reported float when available; estimates do not establish a lower bound |
| `float.upper_bound` | Reported float, otherwise qualified outstanding shares; the bound is not actual float |
| `shares.outstanding` | Security-specific reported supply, otherwise allocated issuer supply |

Estimates require an allocation certificate, positive USD SEC public float no older than 520 days and an eligible price on/before the measurement date within seven days. They carry `sec_market_value_implied_estimate` and confidence capped at 40. Reported float or an estimate exceeding qualified outstanding shares is withheld. Missing allocation, prices or source values remain visible as nulls with reasons.

Original measurement dates remain in the fact records; split-adjustment and constituent evidence remain in provenance. Public availability is based on original input clocks, not processing time. Age and allocation-expiry boundaries also emit qualification changes, so stale estimates do not silently remain usable.

## Corporate revisions

Announced and confirmed-executed ratios are separate metrics. Each execution-date series represents a combined ratio for that security/day. A later correction replaces the earlier ratio; it is not multiplied as another split. A known execution date with no confirmation withholds supply while execution remains uncertain. Corporate parents without complete execution dates/ratio units are rejected.

## Validation and remaining work

Live isolated tests cover provider precedence, lower/upper bounds, split confirmation/correction timing, age and allocation expiry, early-close price selection and insert-free resume. These fixtures prove kernel behavior, not current full-universe source readiness.

The full launcher must still materialize allocation certificates, acquire/certify reported supply and corporate timing evidence, and assemble all producers into the final dataset seal. Raw current provider snapshots or execution dates alone do not supply the missing public-vintage evidence.
