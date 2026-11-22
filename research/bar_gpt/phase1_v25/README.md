# V25: family encoders and light masking

Approved bounded eight-arm campaign, fresh initialization, no teacher anchor.
Shared 384x4 temporal attention (5,433,842 parameters) and separate 272x4 branches
(5,478,098 combined; 2,743,324 price and 2,734,774 activity) use feature attention
width32 per bar. Standalone families export64x32; joint/shared export64x64.
Shared linear decoder64to18; family heads32to12 and32to6. Only learned slots enter
heads. Fusion, when tested, is encoder-side attention over64 concatenated slots.
The old mixed-feature teacher anchor is deliberately removed in every arm.
Loss coordinates are independently normalized per family; remaining V21 raw-log,
body/wick/range-bin/candle and activity-level/change/direction terms are retained.
No masked bars are joined as adjacent observations by the activity-change loss.

Matrix: shared legacy16-visible3/4updates, shared56-visible, shared64-visible,
price56, activity56, separate56, fusion56, second seed of provisional joint winner.
Fresh family seeds reproduce the same initial branches alone and jointly.
Standalone/joint gradients clip separately per family; fusion clips jointly.
Fixed training calibration is copied from verified V24 buffers, not fitted on
development. Existing immutable training114599/development11146 windows retained.
Calibration extraction does not initialize any learned model weight.

Three96-update training-only LR pilots test1e-4/3e-4/1e-3, choose lowest worst-family
relative probe error among finite improving pilots.5%warmup then cosine to1%peak.
Shared mask arms reuse LR; separate branches reuse their standalone LR.
Pilots and tiny-fit updates are reset before actual training. Tiny gate10%loss
decrease is a sanity check, not a claim of near-perfect memorization.
Batch128/256/512 complete-pipeline profiles; batch256 keeps matched updates.
CUDA graph backward is checked against eager gradients before replay. Data is
GPU cached; all preparation, evaluation and checkpoint costs count toward limits.
Maximum1048576 exposures, reduced before main run if profiling requires it.
Each experiment has3500seconds with240seconds evaluation reserve, campaign8hours.
All-visible,8-hidden and48-hidden validation use frozen masks and original positions.
Standalone comparisons omit the unmodeled family; absent predictions are not scored.
The legacy activity reducer emits zero-count entries for price-only runs; the
report treats these as N/A, not measured zero accuracy.
No forecasting usefulness is inferred. Comparison includes all-channel errors,
price bands, activity calibration, directional/range metrics and worst groups.

Use committed, pushed, hash-verified immutable snapshot:
```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location D:\TradingML\codes\bargpt-phase1-v25-20d4213a4
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -u -m research.bar_gpt.phase1_v25.train --output D:\TradingML\runtimes\bar_gpt\phase1-v25-family-mask-reproduction --revision 20d4213a44822d04ecc3ab6bc56e1ffa2c0ca286
```
Run from the deployment root. `--resume` requires exact source/data identity,
restores model/optimizer/RNG/traversal and analytic scheduler position. Human
STOP markers are honored and preserved. Checkpoints last/best remain local;
W&B project `mehdifaraji/bargpt-phase01-v25-family-mask` receives metrics only.
Panel: `python -B -m research.bar_gpt.phase1_v25.panel --output RUN_DIRECTORY`.

Training snapshot is pinned above. Later `compare` and `diagnose` commands and
zero-count summary/panel fixes do not alter that snapshot. Run those read-only
commands from current laptop source, with outputs under the workstation runtime
root. See DECISION.md for results and qualifications.
