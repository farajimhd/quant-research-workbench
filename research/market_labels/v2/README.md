# Spread-aware V66 1b labels

Builds a separate review-only sidecar from the hash-pinned approved V1 1a
labels and sealed `quote-1000.parquet` shards. No ClickHouse connection, market
replay, optimizer change or overwrite of the approved dataset is performed.

Each clock uses the bid and ask closes of its exact completed one-second bar.
Both sides need positive finite prices and displayed sizes of at least one
share, positive event counts, timestamps inside the completed second, and an
uncrossed market. Missing sides are excluded explicitly; no forward fill occurs.
This conservative completed-bar policy differs from the optimizer's persistent
execution-book contract. Every day reports quote coverage and exclusion counts.

For each original 1a ENTRY, retain its original target clock and calculate
`((target_bid-entry_ask)*0.5**(elapsed_seconds/30)-2*0.005)/(entry_ask+0.005)`.
Both original floors apply: net discounted return must strictly exceed twice
the round-trip fee relative to entry capital and be at least 0.1%. The original
target must be an original 1a EXIT; unsupported target witnesses are rejected
explicitly rather than inventing an exit or searching later prices.

Selection chooses the earliest qualifying entry per pair. Overlap groups and
relative allocation are rebuilt with spread-adjusted scores. Individual ENTRY
rows are masked independently. Original first-cluster EXIT rows are repriced
against the selected entry ask and subjected to the same fee/return floors and
five-second minimum. Missing selected-pair quote rows become UNLABELLED;
rejected pairs remain WAIT. Alternative ENTRY target economics are stored per
row; EXIT scores describe the selected canonical entry, not every possible
candidate fill. Any future optimizer integration must consume these explicit
masks and targets rather than infer eligibility from the pair alone.

All original 1a fields, pairs and reference trades are preserved. New columns
include bid/ask sizes/timestamps, target bid, executable and discounted gain,
spread score/threshold, per-entry reason, eligibility masks and canonical EXIT
score. Horizontal chart annotations use the selected ask/bid for new 1b pairs.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B -m research.market_labels.v2.run_build --workers 4 --publish
```

Defaults: source `D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1`,
approved 1a `.../market_shards/labels/v66-1a1b-v1/dataset.json`, output
`.../market_shards/labels/v66-1a1b-spread-v2`. STOP drains active days; reruns
verify sealed days. Only complete 188-session coverage may publish the app
registry. Optimizers remain bound to their existing approved label hash.
