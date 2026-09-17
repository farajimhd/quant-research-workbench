# Causal hindsight-long approximation

`hindsight-long-1s-v1` is an independent long-only policy hosted by the existing
revision-47 Strategy assignment, Portfolio and OMS adapters. It does not change
other policies or consume hindsight labels. The candidate is backtest-only.

## Decisions

- A newly observed completed 1s MACD value above its signal opens a bullish
  episode, including when both values are negative. Equality, bearish MACD, or
  explicitly invalid completed MACD closes it. Forming MACD never opens/closes it.
- Attempt one acquisition per episode, within one second of the episode opening.
  A first observation already bullish counts as an observed episode opening;
  this differs from a full-session label when assignment starts mid-episode.
- Require a noncrossed NBBO at most one second old and spread at most 100 bps.
  Buy at the current ask with that ask as the price ceiling and a 500ms deadline.
  No retrospective low is used as a fictitious execution price.
- Initial broker stop: one tick below the minimum completed 100ms candle low
  over the preceding two seconds, frozen at the episode opening. If fine bars
  are unavailable at admission, use the completed opening 1s candle low.
  Reject stops above the current bid, nonpositive stops, and entry risk over 300 bps.
- Track executable bid peaks after a position is acquired. Arm a trailing exit
  at 100 bps above the actual average entry. The drawdown distance is the maximum
  of 50 bps of peak bid, 25% of the gain above entry, and twice the entry spread.
  The exit boundary can only rise. This trailing exit is strategy-owned; the
  initial stop remains broker protection.
- Exit on drawdown, initial-stop breach, completed MACD episode closure, an
  authorized manual exit, or the 20:00 New York cutoff. Never widen an existing
  pending exit or duplicate its already-working quantity. OMS controls routing.
- Cancel expired/invalid acquisition, including the unfilled remainder of a
  partial fill. Broker callbacks reconcile acquired inventory. Later episodes
  require reentry permission; no additional attempt is made in the used episode.

`src/trading_runtime/hindsight_long.py` owns defaults, validation and state.
Persisted assignment state supports checkpoint restoration. The policy requires
100ms and 1s bars and quotes, with no structural level-book dependency. Historical
quote observations preserve the original NBBO timestamp. The same registered
strategy evaluator runs in the bounded study and normal Backtest paths.

## Candidate and study commands

The candidate helper preserves existing published profiles and infrastructure,
adds a separate profile/run plan, and does not approve a configuration:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& "$env:USERPROFILE\miniconda3\envs\ml4t\python.exe" -c "from src.backend.hindsight_long_candidate import create; r=create(); print(r['candidate_id'])"
```

Configuration validation needs QMD's registered catalogs or their valid saved
configuration records. It fails closed if authority is missing. Do not substitute
an incomplete History catalog for QMD Live's full configuration catalog.

For a bounded canonical-data development comparison:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& "$env:USERPROFILE\miniconda3\envs\ml4t\python.exe" scripts/evaluate_hindsight_long.py --ticker SUGP --session 2026-08-21 --start 09:30:00 --end 09:35:00 --runtime-root D:\TradingML\runtimes
```

The command requires QMD History, caps the window at 15 minutes and the source
at one million events, preserves failure/interruption status, and writes a new
runtime directory per attempt. It merges completed canonical bars with canonical
events chronologically, runs Strategy/Portfolio/OMS/SimulatedBroker, reconciles
actual fills, and compares completed positions with price-only labels afterward.
It records source hashes and market/indicator provenance. It does not read raw
SIP files, activate live trading, or force-fill inventory at the study boundary.

The study uses a simulation-only contract id and an explicitly configured
simulation account with extended-hours permission. It bypasses production
discovery/admission and is not a full-session, broker-identity, live, or holdout
acceptance test. The first episode can be left-censored, the final label interval
is capped at the study boundary, and open positions are reported separately.
The broker snapshot and initial source window determine actual acquisition;
historical extrema are never fed to the evaluator.

`src/market_engine/hindsight_long_review.py` reports missed episodes, unmatched
trades, entry/exit timing, price giveback, per-trade gross movement capture and
net P&L when quantity/fees are supplied. Hindsight is a gross trade-price
benchmark, not an executable or profitability guarantee. Do not optimize only
the chart's optional >5% labels or omit losing/unmatched opportunities.

## Development evidence, September 17, 2026

Corrected SUGP August 21, 09:30-09:35 New York study:

- Four completed positions matched four of eleven positive hindsight long labels.
- Net P&L was -$23 including modeled fees; final inventory was zero.
- Several episodes were rejected for spread/freshness, initial risk or expired
  entry timing. No closeness, profitability, or holdout acceptance is claimed.
- Runtime report: `D:\TradingML\runtimes\hindsight-long\1883dd74-501c-4806-8cb3-cd6402e8b136\report.json`.
  Earlier attempts are retained but superseded: report export and observation
  inventory binding were corrected, then a phantom partial-acquisition latch
  was fixed. Do not use those earlier attempts as policy acceptance evidence.

Saved candidate: `155efc8e-8fe0-4e92-930a-92c74cebc7a3`, profile
`hindsight-long-1s-v1`, run plan `hindsight-long-1s-v1-backtest`. Application
processes need the updated source loaded before running this new contract.
No service was restarted and no approved live configuration was changed.
The saved candidate successfully resolves through
`candidate_runtime_configuration_snapshot('backtest', ...)`. The isolated
default-configuration integration test is skipped when QMD Live's catalog is
unavailable; the saved-candidate check used the valid persisted configuration.

Further research should compare entry/exit timing across multiple sessions,
retain failed settings and all missed/losing opportunities, then evaluate frozen
parameters on untouched sessions. Current defaults are an interpretable starting
point, not an empirically selected optimum.
