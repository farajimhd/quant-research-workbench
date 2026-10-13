# Fundamentals, float and short-pressure data

[Overview](../certified_research_dataset_design.md) · [Previous](05-v7-checkpoints.md) · [Next](07-rl-inputs.md)

Find the required metrics, as-of rules, source qualifications and repairs.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Certified fundamentals and short pressure](#certified-fundamentals-and-short-pressure)
- [Read the three share-pressure concepts separately](#read-the-three-share-pressure-concepts-separately)

## Certified fundamentals and short pressure

### Canonical fact form and downstream lookup

Primary form: long-form change/vintage records and validity intervals, not one copied row per second. Fields include metric ID, value/unit, issuer/security/listing scope, fiscal/measurement period, public availability/precision, revision, source hash/accession, calculation version, confidence, age and missing/conflict reason. Publish batch as-of projections plus pre-open/PM-close/regular-close/AH-close views. Intra-session public updates remain available through intervals; boundary snapshots are not the sole authority.

### Required output families

| Family | Required prepared outputs | Dependency/qualification |
| --- | --- | --- |
| Share supply | Reported float, eligible SEC-estimated float, outstanding shares, lower/upper bounds, weighted basic/diluted shares, issued/authorized shares where reported | Dated identity, original fact/provider vintages, splits; estimate additionally needs measurement-date canonical price |
| Short volume | FINRA CNMS short/exempt/non-exempt/total volume; short-volume ratio; prior-publication value; 20-session summary and configured rolling windows | Daily publication, dated mapping, matching FINRA denominator and coverage; never substitute total SIP volume |
| Short interest | Short shares, settlement date, publication date/time, provider ADV/days to cover; short-interest/float with compatible share basis | Public release evidence; float ratio additionally needs qualified as-of float |
| SEC reported facts | Full current app US-GAAP/IFRS catalog described below, with original tags/units/periods | Original filing vintage, availability, entity bridge, taxonomy and period reconciliation |
| Derived fundamentals | Existing 19 metric formulas below; market cap/share-based turnover where valid | All constituent values available, comparable periods/units and declared share-class basis |
| Related existing reference data | SEC fails-to-deliver, exchange Reg SHO threshold status, splits, dividends, IPO metadata and dated classification/country | Include typed coverage/availability; source timing repair required before causal use |

### SEC reported metric catalog

SEC catalog baseline is the union of `ticker_facts_service.FUNDAMENTAL_TAGS` and SEC/share dimensions in `security_dimensions.py`: revenue, gross profit, operating/net income, basic/diluted EPS, operating cash flow, capex, cash, current assets/liabilities, receivables/payables/inventory, assets/liabilities/equity, current/noncurrent debt, R&D, SG&A, stock compensation, interest/tax expense and effective tax rate, goodwill/intangibles, deferred revenue, debt issued/repaid, equity issuance, outstanding/weighted shares, SEC public float, dividends and repurchases. Freeze every tag alternative, unit, duration/instant rule and formula into an enumerated machine-readable catalog before coding. Today's UI selector is not itself a historical certification authority.

### Derived metrics and approximation rules

Existing derived metrics: free cash flow; gross/operating/net/FCF margins; ROA/ROE; working capital; current ratio; debt/equity; net debt; interest coverage; revenue/earnings/basic-share growth; dilution spread; cash conversion; R&D and SG&A intensity. Preserve current approximation labels, period alignment and invalid-denominator rules. Financial-strength scores remain separately versioned derived heuristics, never reported facts. Market cap cannot indiscriminately multiply one listing price by issuer-wide shares; require compatible class basis or a dated reported snapshot.

The [implemented 19-formula catalog](06a-derived-fundamentals.md) gives exact arithmetic, alignment, source-version and unavailable-state rules. The certified kernel strengthens the old helper's partial-debt fallback: a reported total or both aligned components are required. Currency/framework bases remain separate. Float resolution and the full campaign controller are still under implementation.

### Float resolution and qualifications

Float resolution reuses provider-first logic, qualified SEC public-float estimation, outstanding-share upper bound, then unavailable. `EntityPublicFloat` is USD market value, not shares. Estimated float requires measurement-aligned unadjusted regular price, applicable known splits, unique issuer/class bridge, acceptable age, and consistency with outstanding shares. Multi-class allocation is unavailable without evidence; an upper bound is not an actual float. Preserve reported/estimated/upper-bound/unavailable and conflict states.

The [runnable float/supply kernel](06b-float-supply.md) documents its certified parent inputs, separate bounds, price derivation, timing and remaining acquisition/controller dependencies.

### Current producer gaps

Current gaps: FINRA importer sets publication time null; short data feature SQL falls back to trade/settlement dates; scanner static publication selects latest short records without historical availability; historical loaders use a ticker lookup; float resolution is EOD/ingestion-gated and replaces dated partitions. SEC queries also filter local recorded time. Replace these behaviors in a new research contract, preserving old operational products. Do not merely remove ingestion filters: certify original vintages, original-publication bounds and revisions. Unknown publication times block causal value use; source calendars may provide conservative bounds only with verified evidence.

### Meaning and scope of FINRA short volume

FINRA volume describes disseminated trades reported to FINRA facilities, not all-exchange short volume or outstanding short positions. Preserve CNMS scope and avoid double-counting consolidated/component files. See [FINRA notice](https://www.finra.org/sites/default/files/2019-07/information-notice-051019.pdf).

### Ratios, rolling windows and share basis

Short-volume daily ratio is short volume / matching FINRA total volume. The existing 20-observation summary uses sum(short volume) / sum(total volume), not the mean of daily ratios. The certified version records window dates, eligible publication cutoffs, expected/observed session counts and coverage; partial windows have their own readiness state. Provider days-to-cover remains reported; a recalculated value is a distinct metric using a declared certified ADV window. Short-interest/float and volume/float turnover require positive qualified float, matching security/share basis and as-of availability; upper bounds cannot supply exact ratios.

## Read the three share-pressure concepts separately

### Implementation and current timing gate

The compact producer now supports CNMS daily volume components/ratio and reported interest/ADV/days-to-cover, using dated dataset membership and verified reference receipts. Its source audit rejects unknown publication clocks, invalid date ordering, missing hashes and ambiguous events before writing facts. Public-date-only interest evidence receives a conservative following New York day; trade, settlement and ingestion dates are not availability substitutes.

A read-only October 12 inspection of January 1–October 2, 2026 found 3,143,402 volume rows without public timestamps and 142,612 interest rows without either timestamps or publication dates. These are blocked inputs until timing is repaired with evidence tied to the source version. The launcher's `plan` reports this prerequisite separately from market-source readiness. An official release calendar may establish a date bound only after verifying the applicable publication and source vintage; a scheduled release does not by itself certify an arbitrary later revision. [FINRA reporting schedule](https://www.finra.org/filing-reporting/regulatory-filing-systems/short-interest).

Rolling windows, float ratios and the full reference execution controller remain under implementation. The passing bounded fixture proves admission, publication, denominator and resume behavior; it does not certify the current production rows or full-market performance.

| Concept | Plain meaning | Common misuse to avoid |
| --- | --- | --- |
| Float | Estimated or reported shares available for public trading | Treating total outstanding shares or an upper bound as exact float |
| Short interest | Outstanding short positions at a measurement date | Making the measurement available before its report was published |
| Short volume | Reported short-sale trading activity over a day | Treating that activity as the number of short positions still open |

A downstream lookup should return the latest **publicly available** eligible value, with its measurement date and age. A value can be old yet valid; an unavailable value must remain explicitly missing.

[Overview](../certified_research_dataset_design.md) · [Previous](05-v7-checkpoints.md) · [Next](07-rl-inputs.md)
