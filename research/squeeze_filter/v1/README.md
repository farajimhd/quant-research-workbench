# First thirty-second squeeze expansion study

This is separate observational research. It does not change Torch v2, Strategy
43/44, source products, or the trading grid. It reads existing certified ARTE
30-second candles and the released early-squeeze admission, with pinned preopen
tradable identities and the explicit LGHL exclusion.

Run on the workstation from a committed, pushed and verified deployment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/squeeze_filter/v1/run_study.py --plan
python -B research/squeeze_filter/v1/run_study.py --dates 2026-09-16 2026-09-17 2026-09-18 --session premarket
```

Default: latest three available certified dates, premarket. Use `--dates` for
explicit dates, `--n-sessions` for a bounded latest-date selection, and
`--session regular` or `--session afterhours` for another window. Source catalogue
availability is not full qualification; actual reads verify source attempts,
content, storage and point-in-time tradability. No unavailable date is skipped.

The first complete candle starting after the early signal is eligible. Its
normalized range `(high-low)/open` must exceed both 0.5% and twice the median of
the previous 20 candles, with at least five valid earlier ranges. All values are
CLI parameters. An upward event closes above its open and within the top 25% of
its range; downward events and two-sided expansion remain separate. Only the
first expansion is labeled. Events before the signal are never rewarded.
Unobservable early history is reported as unknown, never a quiet negative.
Missing buckets in a fully certified sparse source mean zero activity, while
OHLC prices remain absent. Candles containing the signal are excluded, including
any move they already made. Session return/maxima are descriptive outcomes and
never feature inputs; first/last observed prices are explicitly named.

Decision features use completed earlier candles only: 30/60/300s volume,
trade counts and dollar volume, activity acceleration, prior return,
session-to-date volume, float, float turnover, time-of-day RVOL, prior range
median and signal age. No outcome-candle volume or range enters the predictor.

Float is `q_live.market_security_float_resolved_v1`, keyed by the certified
symbol ID, with resolution date no later than the study date and insertion
strictly before session start. Current snapshots cannot substitute for missing
historical float. The dedicated reader requires SELECT on that one table.
RVOL uses up to 13 *earlier* certified sessions, comparing cumulative volume at
the same session offset and requiring at least five stable-identity observations.
An unavailable identity/history or zero denominator yields missing RVOL, not
zero or an infinite feature. Source evidence and coverage are saved.

Quantile thresholds and a bounded set of two-feature conjunctions are learned
on all selected dates except the last. Every rule is replayed prospectively:
the first qualifying decision per ticker/session is its only hypothetical
selection. The first upward expansion must start within the next 30 seconds by
default; selecting much earlier is explicitly counted as an early selection.
Thresholds are ranked by training F1, never by validation. The final date
evaluates the frozen rules. The shortlist requires minimum selection support,
at least three training successes and improvement over the unfiltered baseline;
otherwise the script reports no supported improvement. Three dates provide
discovery plus initial temporal validation, not reliable generalization.

Outputs belong under `D:/TradingML/runtimes/squeeze_filter/v1/jobs/<id>`:
`REPORT.md`, `thresholds.csv/json`, exact signal/event and causal decision
Parquet datasets, float as-of evidence, per-session source/hash receipts,
`request.json`, `status.json` and progress events. Repeated runs reverify sealed
source/RVOL caches; interrupted jobs retain complete session files and status.
Restart the command into a fresh job; no incomplete result is accepted as final.

The study reports every known/unknown outcome and per-feature coverage. It does
not simulate fills or establish profitability, and it can discover that no
simple threshold helps. Larger untouched validation sessions are required before
promoting any threshold to a numbered strategy or full trading grid.
