# V52 fresh global net-profit experiment

Copies V50 execution, certified 24-training/12-validation split, sixfold founder sampling, and full controller. Restores V49 lineage selection, crossover, fallback and mutation exactly. Only eligible positive-net-profit candidates can be parents; no-trade and losing candidates cannot mate. Unavailable compatible mates use the V49 fresh-candidate fallback.

Fitness is total training net profit, with zero gross-loss and drawdown penalties. Existing terminal, position-risk and stop-distance eligibility remain mandatory. Risk metrics remain visible. Validation is display-only and is opened after successor/RNG freezing.

The fresh cohort is 6,144 candidates: 3,072 deterministic templates and 3,072 newly sampled random programs, using seed 2021 instead of 2020. All founders are replayed anew. Subsequent population is 1,024 for a 100-generation campaign. No V50/V51 replay receipt or checkpoint is imported.

Prepare on the workstation from the sealed V49 asset job:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v52.full_launch --full --source-job <sealed-v49-job> --output <runtime> --code <committed-code>
python -B -m research.vectorized_backtest.v52.full_run --job <runtime>/full-job.json
```

Source and job hashes are checked before launch; completed generation seals and frozen successor hashes are checked on resume. Write STOP under the run directory for graceful interruption.
