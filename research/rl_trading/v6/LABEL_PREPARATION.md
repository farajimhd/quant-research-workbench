# Current V6 label authority

Current training requires `rl-v6-swing-opportunity-dataset-v2`, using the
chart-verified `price-action-long-opportunities-v2` algorithm. Old portfolio
teachers, rolling-15 episode windows, fee-adjusted candidate targets, bracket
sidecars and checkpoints from their datasets are rejected by current loaders.
Historical files remain audit evidence, not current supervision.

Generate every saved forward-bank listing on the workstation:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v6/run_prepare_labels.py --source-manifest D:/TradingML/runtimes/rl-v6-forward-40cd11fac/day-roots.json --output D:/TradingML/runtimes/rl-v6-swing-labels-v2 --workers 8 --listings-per-shard 32
```

The same command and exact producer source resume verified completed shards.
Archive snapshots must also pass `--source-commit` with their full pushed SHA.
`progress.json` reports
day, active, queued, completed and failed units. All 19 saved days (context,
16 train, two development) must complete before `dataset.json` and
`D:/TradingML/runtimes/rl-v6-active-labels.json` are published. August 26 is
not read. Immutable bank bytes are verified before worker dispatch. Every
valid observed OHLC/MACD candle in the full saved day is labelled; invalid
price rows and all-invalid listings are counted. There is no old candidate
filter, filling, fee adjustment or synthetic padding.

Labels retain raw `entry_gain`, `exit_gain`, qualities, reference actions,
conditional alternatives and separate next-pair carry. Raw gains are dollars
per share. Teacher value heads keep their existing bps interface through
explicit `entry_gain/current_close*10000` and
`exit_gain/reference_entry*10000` conversion, never quality-as-value.
Stop/target references are not silently attached as old bracket targets.
Classification keeps quality/complement soft targets; hard actions use 90%.
Teacher training requires ticker heads and WAIT/HOLD transport. Preparation
does not train a teacher/PPO model. Old normalization and checkpoint dataset
bindings remain invalid until explicitly rebuilt.

Final publication additionally requires `publication-audit.json`: a separate
full source census, clock/scalar byte verification, receipt audit, exact
recomputation of representative liquid/sparse listings on every day, and
bounded real training-adapter checks. Existing rank settings (top 1000,
one-second refresh) are retained. The held observation is hypothetical;
reference quantities may be fractional for prices exceeding the $10,000
bookkeeping balance. Quantities never scale raw per-share value targets or
claim a fill.

Atomic JSON replacement retries transient Windows reader locks for at most
two seconds. Explicit `--reuse-receipts-from-source <immutable snapshot>`
allows controller-only recovery when every shard is already complete, the
numeric algorithm files match byte-for-byte, and both worker function ASTs
are unchanged. All receipt bytes/counts still verify. Missing receipts or
changed numerical producers fail closed; no mixed-producer generation occurs.
