# V21 range and activity loss experiments

## Outcome and limits

Joint feature attention and temporal attention with one decoder can improve
reconstruction, range classification and activity scale. Differentiable metric
surrogates helped, but no candidate dominates every previous model. Hard sign
and bin accuracy remain evaluation metrics, not directly differentiable losses.
This campaign does not establish forecasting usefulness or authorize treating
reconstruction accuracy as forecasting accuracy. No new 20-epoch run was launched.

The retained fully evaluated candidate is `phase1-v21-body-20261121/body_direction`
under the workstation BarGPT runtime root. Checkpoint SHA256:
`a5279ed6fd2fd6ca44cf5ab14573a4383f9f66a589ed3d8c37c64caad28300d5`.
Its training source is `a3f1e6ee689231552fb190c39e5c365de2bcc0fd`.
Decoder-free exports and their manifest are in its `encoder-exports` directory.

## Model and data

Feature attention produces 16D per bar. Temporal attention exports 512D per
64-bar window. A single linear 512-to-1152 head reconstructs all 64x18 features.
Total parameters: 5,330,400; decoder: 590,976. No raw/reference bypass or appended
embedding fields. Fixed training-only transforms and FP32 output remain unchanged.
Both attention encoders train in joint experiments; final body calibration
changes only existing body-coordinate decoder rows.

The unchanged split hash is
`d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
Full training has 186,575,722 windows; validation has 47,750,007.
Bounded experiments use 114,599 training windows and 11,146 development windows,
combining the original V8 subset with the training-only V19 supplement.
Their hashes, frozen teacher, calibration and parent hashes are in each manifest.
Sampling is uniform over this training pool; development is not a population
estimate. Training uses 16 random visible bars in three quarters of updates and
64 visible bars in one quarter. Source-valid masks are preserved. No difficult
valid examples were discarded or clipped for metrics.

## Experiments

Runtime directories below are relative to `D:/TradingML/runtimes/bar_gpt`.

| Experiment | Finding and decision |
|---|---|
| `phase1-v21-protected-r2-20261121` | Frozen encoder, selected existing head rows. Log activity loss improved daily bid counts; multiplicatively symmetric loss worsened them. Reject symmetric loss. |
| `phase1-v21-metric-log-20261121` | 6,000-step matched range/activity calibration. Ordinal range BCE and activity-direction BCE improved selected accuracy; plain log loss remained competitive. Both regressed daily trade volume. |
| `phase1-v21-selective-20261121` | Restore parent trade-volume rows in the same head, zero training updates. Protected parameters exact; 1,473 protected metrics agree within numerical roundoff. |
| `phase1-v21-joint-20261121` | Joint control versus small added metric losses, 2,000 steps each. Both drifted in direction/counts; adding losses alone was insufficient. |
| `phase1-v21-joint-repaired-20261121` | Replace overlapping activity penalties with log level/change and direction losses; retain price/body/wick/latent objectives. Daily count improvement survives joint updates. |
| `phase1-v21-body-20261121` | Calibrate only body rows, 2,000 steps; recovers some candle direction without changing embeddings, close or activity outputs. Retained full-validation candidate. |
| `phase1-v21-joint-separated-20261121` | Same predictions and loss values as joint_repaired, but route level/range/candle gradients separately. Completed 2,000 steps; worse close, range and candle direction. Reject. |

The final matched routing test used source
`6454ba99e89bd0b35b8b28a4e487283d49bce684`, same selective parent, seed, batch and
learning rates as joint_repaired. Development close MAE was 0.23428 versus
0.22770, masked close 0.23148 versus 0.21152, candle direction 90.752% versus
90.834%, and daily bid range-bin accuracy 78.630% versus 79.423%. Volume
direction improved only 0.005 points. Routing did not fix the observed drift.
Its tiny-case loss fell from 123.56 to 45.35; cached throughput was about
74,493 windows/s. W&B run: `n7e08pca` in the same V21 project.

The user's conditional approval for 20 epochs is recorded, but this campaign
does not yet meet the no-material-regression gate. Keep the fully evaluated
candidate for research; do not automatically start full training. The unresolved
decision is how to improve masked range and flat/moving classification without
giving back price/count reconstruction gains. More epochs alone are not an
evidence-backed fix.

Development daily bid-count MAE improved from 35,889 (V20 parent) to 24,513;
daily bid range-bin accuracy from 68.05% to 79.31%. These are development results,
not the full-population figures below. Parent comparisons are not all
transformation-only ablations; architecture/initialization history differs.

Early CUDA-graph failures (CPU-to-device constants inside capture) are retained
as failed attempts, not completed scientific results. GPU-buffer constants and
cached range edges fixed them. Every captured gradient or metric buffer was
checked against eager execution. Five focused CPU tests passed in the latest
gradient-routing source revision.

## Full validation

`phase1-v21-full-validation-20261121/result.json` covers **47,750,007/47,750,007**
windows. Evaluation source: `d44da2a5ce445a021342e72ddbcc77c85f3b40f1`.
W&B: https://wandb.ai/mehdifaraji/bargpt-phase01-v21-repair/runs/1xdxqxlt

| Full-population metric | V18 joint | V19 adapter | V21 retained |
|---|---:|---:|---:|
| All-visible 1s close MAE, dollars | 0.74190 | 0.55283 | 0.39902 |
| Masked 1s close MAE, dollars | 1.20888 | 0.69081 | 0.41573 |
| All-visible 1s candle moving-direction accuracy | 87.30% | 92.06% | 90.77% |
| All-visible 1s volume moving-direction accuracy | 73.65% | 79.90% | 79.82% |
| All-visible daily bid-count MAE | 37,933 | 46,599 | 30,235 |
| All-visible daily bid range-bin accuracy | 74.01% | 73.07% | 81.39% |
| Masked 100ms bid-size MAE | 41,195 | 5,823 | 5,743 |

All 216 timeframe/channel/masking raw-MAE groups improved versus V18. This
does not mean every accuracy metric improved: masked 1h trade range-bin accuracy
fell 4.89 percentage points versus V18. Against V19, masked daily volume MAE
increased 6.42%, daily quote-count direction fell about 1.59 points, and 1s candle
direction fell 1.29 points. Exact-zero/flat recall remains weak. No nonfinite
metric counts occurred. Complete metrics and worst-group comparisons are in
`metrics.json` and `comparison.json`; selected averages must not hide tails.
The reusable `phase1_v21.compare_full` command accepts a candidate `result.json`,
one or more `--reference` result paths and `--output`; it requires completed
full-population receipts and equal counts. Its saved comparison confirms
203/216 raw-MAE groups improve versus V19. Split and masking provenance must
also be checked in manifests; equal counts alone do not establish equivalence.

Validation took 621.45 seconds (76,837 windows/s including startup), with
7.07-second startup, 5.73 seconds cumulative loader wait, and 3,319 MiB peak
allocated GPU memory. Tested batches 4,096/8,192/16,384; selected 8,192 with
16 workers. The largest batch was slower. Cached training profiling is a
different workload and must not be quoted as full-loader training throughput.

## Embedding probe and reproduction

Training-only linear-readout probes in `phase1-v21-bar-probes-20261121` show
standalone 16D scale retention: 1s close MAE 0.01639 versus original Phase0
0.01654; daily bid-count MAE 16,388 versus 16,471. Final body calibration leaves
both encoders unchanged. These small probe improvements are not future-label
or trading evidence.

From the pinned evaluation snapshot, with `PYTHONDONTWRITEBYTECODE=1`:

```powershell
python -B -m research.bar_gpt.phase1_v21.validate --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v21-body-20261121/body_direction/checkpoint.pt --output D:/TradingML/runtimes/bar_gpt/phase1-v21-full-validation-reproduction --revision d44da2a5ce445a021342e72ddbcc77c85f3b40f1 --batch-size 8192 --workers 16
```

Use a new output identity. Preserve existing checkpoints, receipts and STOP
markers. Per-trial manifests, curves, optimizer/RNG checkpoints, W&B IDs and
the campaign ledger are under `phase1-v21-campaign-20261121` and each run.
The existing older full-training runner is not compatible with this one-head
architecture; it must not be reused silently.
