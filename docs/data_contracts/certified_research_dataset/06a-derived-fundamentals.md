# Certified derived fundamentals

[Fundamental families](06-fundamentals-short-data.md) · [Exact fact schema](12c-schema-facts-state.md)

## Output and source contract

`aligned-public-fundamentals-v1` publishes the existing app's 19 arithmetic formulas as compact ClickHouse change/vintage records. The source arithmetic fingerprint is `490bbde642e7e3fb259ba83b590d0a355324e498de72683e2a7bd2a88c204416`. Source changes block the producer until the implementation is reviewed and versioned. Financial-strength scores are separate heuristics and are not produced by this kernel.

Reported parents must have verified receipts. Inputs retain period start/end, context kind, original availability, reported fiscal metadata, unit/basis and accounting framework. FY metadata is preserved as reported; it is not inferred from the measurement calendar year. Generated receipts record processing time separately from original-public information availability.

## Calculation catalog

`a`, `b`, `c` follow the input order below. `previous` is a prior comparable observation. `debt` is a reported total, otherwise the sum of both aligned components. Native currency amounts remain in that currency. Ratio and percent outputs preserve their currency/share basis separately.

| Metric | Inputs in order | Formula | Output unit |
| --- | --- | --- | --- |
| `free_cash_flow` | Operating cash flow; Capital expenditure | `a-abs(b)` | native currency |
| `gross_margin` | Gross profit; Revenue | `100*a/nullIf(b,0)` | percent |
| `operating_margin` | Operating income; Revenue | `100*a/nullIf(b,0)` | percent |
| `net_margin` | Net income; Revenue | `100*a/nullIf(b,0)` | percent |
| `free_cash_flow_margin` | Operating cash flow; Capital expenditure; Revenue | `100*(a-abs(b))/nullIf(c,0)` | percent |
| `return_on_assets` | Assets; Net income | `100*b/nullIf(a,0)` | percent |
| `return_on_equity` | Stockholders' equity; Net income | `100*b/nullIf(a,0)` | percent |
| `working_capital` | Current assets; Current liabilities | `a-b` | native currency |
| `current_ratio` | Current assets; Current liabilities | `a/nullIf(b,0)` | ratio |
| `debt_to_equity` | Stockholders' equity; Total borrowings; Debt current component; Debt noncurrent component | `debt/nullIf(a,0)` | ratio |
| `net_debt` | Cash; Total borrowings; Debt current component; Debt noncurrent component | `debt-a` | native currency |
| `interest_coverage` | Operating income; Interest expense | `a/nullIf(abs(b),0)` | ratio |
| `revenue_growth` | Revenue | `100*(a-previous)/nullIf(abs(previous),0)` | percent |
| `earnings_growth` | Net income | `100*(a-previous)/nullIf(abs(previous),0)` | percent |
| `share_growth` | Weighted average basic shares | `100*(a-previous)/nullIf(abs(previous),0)` | percent |
| `dilution` | Weighted average diluted shares; Weighted average basic shares | `100*(a-b)/nullIf(b,0)` | percent |
| `cash_conversion` | Operating cash flow; Net income | `a/nullIf(b,0)` | ratio |
| `research_intensity` | Research & development; Revenue | `100*a/nullIf(b,0)` | percent |
| `sga_intensity` | SG&A expense; Revenue | `100*a/nullIf(b,0)` | percent |

## Alignment and unavailable states

- Exact calculations require identical known measurement spans, native units and accounting namespaces. A reported value with unknown context cannot establish a duration.
- Duration selection prefers FY observations spanning 300–400 days; otherwise it uses the latest known fiscal duration. Growth requires a prior matching fiscal-period code and a span length within seven days. It is change between comparable observations, not an annualized CAGR.
- A newer potentially annual observation with missing context withholds the corresponding result. It cannot silently preserve an older derived value. Missing primary inputs are explicit unavailable records on the known basis.
- ROA/ROE preserve the app's point-in-time approximation: latest comparable income over latest assets/equity. They retain both source spans, carry an approximation reason and cap confidence at 60. They do not imply an average-balance calculation.
- Debt requires a reported total or both aligned current/noncurrent components. The old helper's single-component fallback is intentionally withheld because it cannot establish total debt.
- ROE and debt/equity require positive equity. Other app ratios require a nonzero denominator; interest coverage uses absolute interest expense. Invalid/missing inputs and nonfinite results produce nulls with reasons.

## Storage, lookup and validation

Only changes in constituent evidence or qualification emit new rows. Filing revisions remain separate vintages. No training shards, optimizer products or local data exports are created.

As-of lookup returns a latest record **per output unit, basis unit and namespace**. Callers can select these axes explicitly. It never chooses an arbitrary currency, combines frameworks or performs implied FX conversion. A null qualified result takes precedence over an older valid result on the same axis.

Live isolated tests validate all 19 independent numeric examples, annual-versus-quarter selection, later revisions, incomplete debt, missing context, EUR/IFRS separation, missing primaries and insert-free resume. A bounded real AAPL SEC→derived chain also runs entirely inside ClickHouse apart from metadata/proof readback. Full-universe throughput and full campaign launch are still pending.
