# Research UI: teacher label inspection

The app's **Research** route selects V6 and a training/development session,
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
Original long MACD episodes appear as green regions; saved MACD channels appear
in the shared oscillator pane. One half-size marker is displayed per labeled
candle for the selected episode and training branch. No combination or priority
is applied across independent contexts. All source rows of that context remain
in details. EXIT uses a red down arrow above its candle with reward above; other markers
and reward numbers appear below. WAIT has no reward text. ENTRY numbers are the
original discounted candidate score; HOLD/EXIT numbers are fee-adjusted profit
per share used to derive exit quality, not a discounted entry score or PPO reward.
Route navigation retains the audited workspace, filters, container layouts and
a bounded 32-window response cache for the app session; reload requires preflight. Labels cannot be edited in this workflow.

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
