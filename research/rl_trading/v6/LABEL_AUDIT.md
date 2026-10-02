# Research UI: teacher label inspection

The app's **Research** route is pinned to the user-selected matched V6 RTH
diagnostic `rl-v6-rth-matched-v6-b31fc0cdc`: training July 31, August 10/21,
development August 24/25, 09:30 inclusive to 09:47:04 exclusive New York time.
Pre-09:30 candles were causal warmup, not optimization labels. The route selects
one of these sessions,
preflights saved original episode labels, and opens three Canvas windows:
reserved architecture details, label statistics, and a read-only candle/label
chart. It does not launch teacher training or PPO and cannot select Aug26.
The research API is `/api/research/models`; its V6 implementation is
`label_audit.py`. Restart the backend after deploying this source to load the
new router; the frontend does not require a published trading Canvas profile.

`RL_V6_AUDIT_RUNTIME_ROOT` explicitly configures the mount for the workstation
runtime. Its default is `\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes`.
The adapter maps only certified `D:/TradingML/runtimes` paths within that mount.
It pins the existing `rl-v6-training-ready-c824232b4/audit-cert-repair-df08998e3`
dataset and `rl-v6-episode-windows-aa9021599` original episode sidecars; a
different release requires an explicit source adapter change, never a search
for arbitrary newest files. Missing or changed sources fail closed.

Preflight verifies selected-session label hashes/counts, role, episode identity
and bank-certificate binding. It is an inspection readiness check, not a full
training/serving certification. Whole clock and scalar files are SHA-256 checked on first chart load per session;
fingerprint-keyed caches retain up to 18 sessions. Unused level tensors are not
read or certified by this chart. This reduced measured cold chart loading from
80.6 seconds to 22.6 seconds on the workstation mount; warm loading was 0.14 seconds. Charts decode the original float32 log OHLC channels and display
actual valid-price 1s candles. Invalid-price activity rows are counted explicitly,
and their targets remain in the detail table without snapping to nearby candles.
Decision timestamps refer to candle close; plotted candles start one second
earlier. Charts use 15-minute pages and preserve overlapping episode rows.
Both Research paths now shade observed 1s MACD sign episodes: green for
MACD >= signal, red for MACD < signal. Shading is independent of unchanged
saved teacher targets. Saved MACD channels appear in the shared oscillator pane. One half-size marker is displayed per labeled
candle for all training episodes by default, or the selected episode and branch.
Overlapping same-clock rows share one marker; text lists distinct saved
probabilities. Different hard targets use a square, without selecting or
averaging contexts. All source rows of that context remain
in details. EXIT uses a red down arrow above its candle with soft target probability above; other markers and probabilities appear below.
HOLD uses blue dots; WAIT uses gray dots. WAIT with zero ENTRY probability has no text. This run trained classification
only: marker numbers are the saved probabilities, not source score/profit
calculation inputs or a PPO reward. Source calculation inputs remain in details.
Sidebar navigation hides Research while keeping its canvas and native chart
mounted, preserving the audited workspace, filters, chart view and container
layouts without another preflight or chart request. A bounded 32-window response
cache also retains loaded chart responses for the app session. Container geometry and
closed state persist in Research-only browser local storage across reloads;
reload still requires a fresh data preflight. Labels cannot be edited in this workflow.

Statistics distinguish raw hard-class rows (`p >= 0.5`) from original episode
weight times soft probability. They exclude training class-balance multipliers.
Median episode span is last minus first observed target time, not continuous
candle coverage. WAIT coverage outside supervised episodes is unknown.
Probability bins are upper-exclusive except the final bin, which includes 1.0.
Held branches describe hypothetical positions.

Browser acceptance uses real labels through the managed frontend launcher:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/run_frontend.py ui:review -- --research-teacher --output-dir D:/TradingML/runtimes/rl-v6-label-audit-ui-review
```

Optional `--api-url` selects an isolated research-only test router without
restarting an active trading backend. Review covers light/dark, UI scales
0.8/1/1.25, normal/compact viewports, train/development sessions, branch/episode
selection, container close/restore/reset, fullscreen and preflight invalidation.

Run verification compared all 22,775 saved development target pairs with the
original episode files: zero mismatches. Original RTH class counts match all
36 training sessions (12 epochs x 3 days): 14,654 / 18,075 / 10,331 rows.
The snapshot episode loader source text equals current source after newline
normalization; execution attachment source hashes also match. This verifies
source/scope identity, not whether hindsight target semantics are useful.

The separate **Price-action experiment** path displays an isolated NVDA full-RTH
pair-local swing-opportunity label product with bounded quality scores. See [PRICE_ACTION_LABELS.md](PRICE_ACTION_LABELS.md)
for its zero-cost algorithm, source, parameters and reproduction command.
