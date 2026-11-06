# Inherited reference

This document is retained from V64 for design provenance. Historical performance
and qualification statements below are not V67 results. See README.md for V67
input, initialization, and feature changes; workstation evidence lives in its runtime.

# Observer quality pages

Open **Y**, or **A → 5**, without changing optimizer state.

- **1 Population:** initial founders versus current population, new offspring and survivors. **T** switches training/validation; **X** selects a cohort; **Z** switches median/p90/maximum. **I** selects initial results, **C** follows latest, **[ / ]** selects a generation.
- **2 Generations:** population score, profitable/inactive fractions, best-so-far and median improvement. Only sealed generations with complete matching session scope contribute to trends.
- **3 Generalization:** the same top 1/10/100 candidates selected by training score, with per-session normalized training/validation performance. Validation never affects selection. Partial paired results are explicitly provisional.
- **4 Groups:** **F** cycles origin, template family, crossover/operator and immediate-parent lineage. Enter opens candidates, then sessions, then the existing sortable positions/order details. Groups may overlap.
- **5 Attempts:** published reproduction telemetry, generation/critical-path timing, replay worker seconds and median-score gain per wall hour. **D** retains the existing detailed timing page.

Inactivity means no first entry fill in the observed sessions, including partial fills. Its exclusive reasons are no entry signal, signal without a request, and request without a fill. Missing counters are unavailable. Holdings and win rates use closed positions; zero trades do not imply a zero win rate. Distinct trade ledgers measure identical closed-position listing/time/profit records, not equivalent network behavior.

Profit concentration divides the largest positive session or ticker-date net profit by the sum of positive profits at the same granularity. No positive profit yields unavailable, not zero. Drawdown is the sum of session drawdowns used by the existing objective. Before-commission profit adds recorded order fees back to net profit and still includes bid/ask execution effects.

Parent improvement requires new candidate IDs, both parents' recorded scores and identical complete session coverage in the prior generation. Retention requires a published next population. Duplicate genomes use program hashes. GPU-active hours are not recorded and remain unavailable; replay worker time includes concurrent-lane waiting and must not be described as GPU time.

A separate, low-priority CPU process incrementally reads one completed receipt at a time and scans each batch order journal once for fees. Disposable summaries live under `observer-quality-v1` in the campaign runtime. The UI consumes asynchronous cached snapshots; it never waits for receipt decoding, journal scanning, aggregation or replay. Missing validation, unfinished generations and cache errors remain visible. This observer neither imports a GPU executor nor writes optimizer checkpoints, populations, receipts or control files.
