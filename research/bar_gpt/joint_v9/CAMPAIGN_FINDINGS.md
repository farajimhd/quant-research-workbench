# V9 temporal representation campaign — 2026-11-17

27 experiments support retaining **E26 as a reconstruction research encoder**:
patch attention, a 512-dimensional learned embedding and a single affine
decoder. Scale recovery improved, but future probes and direct forecasting did
not consistently beat strong controls. **No all-round forecasting winner was
established; full forecasting training is not recommended yet.** Validation
reconstruction remains important supporting evidence, not a substitute for the
measured future-task results.

## Scope and provenance

The finite campaign compared patch, bar-token and variable-token attention,
temporal convolution, an MLP control, larger capacity, CLS versus flattened
patch readouts, masking, pure reconstruction versus joint training, frozen
transfer, separate learned context dimensions, activity coordinates, fitted
linear readouts and fixed horizon queries. Later combinations are explicitly
not isolated ablations. The latest unrestricted research request superseded
the original eight-experiment count; the eight-hour stage and sixty-minute
individual experiment bounds remained in force. No full-data run was launched.

Workstation runtime: `D:/TradingML/runtimes/bar_gpt/joint-v9-20261117`.
The runtime `COMPARISON.md`, `comparison.json`, `decisions.json`, per-run
manifests and `learning.jsonl` contain the complete ledger. Native errors for
all 18 channels are in `all-channel-comparison.csv` and each result JSON.

Certified Phase 2 authority:
`phase2-all-published-certification-20261114/release-v1/certificate.json`, SHA256
`87a1235d9d925c7167d3ac210d40a72c9c63977ebbe7e650fa508e13dfd000d2`.
Initial dataset hash:
`ab7da0c6f52d54601534ceeeadc09f44b31342569ba8f598cbb0430fd99869b0`.
Expanded dataset hash:
`642f24f9be0f77b42b0be9c05ce86701ff92c6b26156dbd8ee61a18dfd8b13bf`.

Expansion retained all 9,600 original training origins byte-exactly and all
holdouts unchanged. It contains 90,156 training origins, 22,539 sessions and
3,384 tickers from 2019–2023. Development and excluded-ticker development each
have 960 origins/240 sessions. Audit 2025 has 952 origins; audit 2026 has 924
origins/231 sessions and only 32 tickers. Sampling covers six price bands,
year/liquidity strata and at most four disjoint future supports per session.
Historical inputs can overlap within a partition. Calibration, training and
metrics use uniform retained origins, without population reweighting.

All later candidates reuse E02's original **training-only** calibration;
its manifest SHA256 is
`de89f501117d4edf5337c4cd23be8b8283c0b495d58c0cbbd6bc1d3bab14a908`.
Source payloads and split/validity semantics were preserved. No historical
flatfiles or new market-event queries were used. The audits are sealed for this
campaign but not pristine external tests of all prior research.

## Hybrid encoder design

E26 is a reconstruction-pretrained attention research candidate. E27 freezes
that encoder and a training-fitted anchor readout to test separate forecasting.

- Input: 64 bars, each with 18 market values and nine time features, plus their
  27 source-valid flags. Prices use log; quantities use log1p. Invalid entries
  never contribute to normalization or loss.
- Shape path: visible-only centering, fixed log-relative price units and
  square-root-of-log1p activity coordinates; asinh shape compression. Each
  eight-bar patch contains `8 × (27 values + 27 validity flags) = 432` inputs,
  projected to width 128. Two pre-normalized attention layers, four heads,
  eight ordered patch tokens plus a CLS token. The eight patch outputs are
  flattened and projected to 352 learned dimensions.
- Context path: 138 visible-only mean/coverage/latest/activity-basis/dispersion/
  return features pass through learned FP32 projections to 160 dimensions.
  They are **not copied latent slots** or decoder side inputs.
- Export: **512 learned dimensions**, 352 shape plus 160 context; **862,304
  encoder parameters**. Zeroing the encoder removes input dependence.
- Reconstruction: one FP32 affine layer, **1,772,928 parameters**, producing
  64 × (18 market codes + nine metadata values + 27 validity logits). Fixed
  structured inversion enforces OHLC/paired-quote geometry and nonnegative
  activity, followed by FP64 raw-unit inversion. This is a shallow decoder,
  although it has more parameters than the encoder. It needs no true local
  reference, future value or target-derived statistic.
- Masking: 25% of training windows retain 16 uniformly selected random bars;
  the other 75% are fully visible. Hidden values **and metadata** are excluded
  from encoder context. Patch positions remain; this is not physical removal
  of 48 transformer tokens. All source-valid targets remain supervised.

Phase 2 accepts sixteen embeddings (8×1s, 4×10s, 2×1m, 1×1h, 1×daily), their
fixed slot/timeframe identities and causal ages. Two attention layers at width
512 produce direct forecasts for the next 64 **observed** 1s bars. All contexts
end by the prediction origin; all labels begin afterwards. Bidirectional
attention over completed history does not expose future data. No autoregressive
target feeding is used. The standalone forecaster includes a small learned
anchor readout, not the Phase 1 reconstruction decoder or raw historical bars.

## Confirmed findings and limitations

- Patch attention was the strongest short-screen forecasting family; this
  does not establish that MLPs cannot generalize. Wider attention and horizon
  queries did not win under their measured schedules.
- Historical-anchor E01–E10 are causal conditional controls, **ineligible** as
  embedding-only forecast recommendations. E11 onward recover scale through
  the embedding.
- E16 pure reconstruction improved full-visible error but had poor masked
  transfer. E20 always-16-visible training reversed that tradeoff. E24 mixed
  visibility largely avoided the distribution mismatch. This is measured
  evidence, not a claim that masking is inherently harmful.
- Linear readout fitting exposed recoverable scale information that the
  original decoder had poorly recovered. An all-coordinate fit failed on
  difficult valid training tails and was rejected without clipping or dropping
  them. The retained fit changes only linear level/activity code coordinates;
  nonlinear geometry rows remain unchanged. Fit labels are training inputs,
  never validation statistics or future targets.
- Strong historical activity mean/median controls remove most apparent gains
  over last-bar persistence. Price-return forecasting remains difficult.
- Frozen ridge probes use fixed alpha=100 and training-only standardization.
  Full-vector, architecture-defined context-only, raw-summary and untrained
  comparisons expose sensitivity to dimension and readout conditioning. They
  do not prove that information is absent merely because a linear probe fails.
- Phase 2 has five timeframes. **100ms is absent from training**; the separate
  six-frame Phase 1 audit is transfer evaluation, with 100ms explicitly zero-shot.
- Experiments use seed 17. Adaptive development selection, one training seed,
  overlapping historical contexts within partitions and limited 2026 ticker
  coverage restrict certainty. Paired session intervals are not seed variance
  or a correction for all research selection.

## Pinned audit outcome

**Retain E26 as the best-supported reconstruction research encoder, not as a
proven forecasting improvement. Do not start full forecasting training from
these results.** E24 remains a smaller, competitive control. E25 is the stronger
fitted-readout forecasting control, but its small activity advantage does not
survive 2026. E27's frozen transfer is rejected as a forecasting winner.
Pins were written to `finalist-pins-before-audit.json` before opening the audits;
no architecture or objective was tuned afterwards.

Balanced price-relative reconstruction error (lower is better):

| Evaluation | E24 full / masked | E26 full / masked |
|---|---:|---:|
| 2025 Phase 2 audit | 2.781% / 4.006% | 2.362% / 3.861% |
| 2026 Phase 2 audit | 1.049% / 1.552% | 0.907% / 1.510% |
| Six-frame Phase 1 transfer audit | 1.115% / 1.366% | 1.089% / 1.401% |

Masked metrics evaluate **all 64 reconstructed bars after showing only 16**,
not only the 48 hidden positions. Phase 1 transfer activity log MAE improves
from .3180 to .2547 full-visible and .6771 to .6652 masked. E26's 2025 price
reconstruction beats the input-reading visible-median control (7.934%) and
training constant (784.2%). These controls are not compact learned embeddings.

E26 Phase 1 transfer trade-close raw MAE: 100ms $0.1497 (zero-shot), 1s $0.1302,
10s $0.1145, 1m $0.09045, 1h $0.4735 and daily $0.5644. All 18 channels, relative
means/medians/P90s, bands, zero/tiny denominator counts, raw/log activity errors
and prediction/target variances are preserved in `final-audit-all-channels.csv`
and the audit JSONs. Zero/tiny denominators are counted and excluded only from
undefined relative ratios, not raw MAE or source-valid supervision.

**Worst-group regressions prevent an unqualified recommendation.** In the
six-frame transfer audit, E26's worst price group is 26.78% versus E24's 25.41%
(hourly bid-low below $0.10, 133 valid targets). Its hourly close MAE is also
worse ($0.4735 versus $0.3883). In 2025, E26's daily ask-high below $0.10 has
50.47% mean relative error (233 targets); the worst E24 group is 41.75%.
No such values were clipped or removed. E26 has zero nonfinite, negative or
OHLC/quote-structure violations in the reported native reconstruction audits,
but this is not uniform reconstruction accuracy. Time metadata and validity
are also imperfect; their errors/confusion counts are in each audit result.

Direct future prediction, percentage reduction in training-scale-normalized
log/log1p MAE versus causal controls (raw-unit errors are separately retained):

| Model / audit | Price vs last bar | Activity vs history mean | Activity vs median |
|---|---:|---:|---:|
| E25 / 2025 | -1.92% | +1.16% | -0.01% |
| E25 / 2026 | -3.82% | -1.07% | -2.80% |
| E27 / 2025 | -2.00% | -0.21% | -1.39% |
| E27 / 2026 | -6.95% | -1.08% | -2.81% |

E25 activity versus mean has paired-session 95% intervals [+0.59%, +1.73%]
in 2025 and [-1.72%, -0.46%] in 2026. Both candidates also lose to flat-last-close
price forecasts. E24/E26 future heads are untrained and excluded from forecast
claims. Their diagnostic forecast numbers must not be ranked as trained models.

Configured-GPU-precision frozen probes confirm improved **scale retention**,
but no consistent gain in **future generalization**. E26/E27 share an exactly
preserved encoder. On unseen-ticker development, trained versus untrained
window probes have price-scale log MAE .00438 versus .01310, activity-scale
log MAE .06416 versus .11299, and within-window log-return MAE .000331 versus
.000356. Volatility/spread retention does not improve consistently.

For future activity-level R² on 2025, the hybrid context-only probe gives .7363,
untrained context .7406 and raw summaries .7498. Future volatility R² is .3286,
.3609 and .3982 respectively. The full-vector probe is weaker still. On 2026,
full-vector volatility/range/return R² becomes negative; context-only readouts
are much more stable, but still do not consistently beat raw summaries or the
untrained context projection. Full details and eligibility counts are in
`gpu-final-probes-development.json` and `gpu-final-probes-audit.json`.

Thus training improved scale recovery and reconstruction, while **the desired
forecasting advantage remains unproven**. This does not disprove reconstruction
pretraining generally: budgets, capacity, masking and objectives differ across
some comparisons, and full-data/multiple-seed training was not performed.

The retained E26 checkpoint is step 4096. E25 selects step 2560; E27 selects
step 512, although both training runs completed 4096 updates. Later E27 price
performance deteriorates substantially. `learning-curves.png` and the immutable
learning logs expose this; the selected checkpoint is not represented as the
last optimizer state. E26 full-visible reconstruction price error is 1.537%
on the fixed training sample, 2.044% development, 1.940% unseen-ticker development
and 2.362% audit 2025. The smaller 2026 audit has a different, easier composition.

## Engineering evidence

27 training experiments completed, each below eight minutes including its
preparation, tiny fit, profile and final evaluation; the finite stage remained
below eight hours. One early admission collision was rejected before GPU
training and retried under a distinct identity. Rejected readout fitting and
an incomplete batch profile remain separately recorded. No timed-out partial
evaluation is reported as complete.

Hardware: workstation RTX PRO 6000 Blackwell Server Edition, approximately
95.6 GiB physical GPU memory, 64 physical/128 logical CPU cores, PyTorch
2.12.0+cu132. This is not the laptop GPU. The expanded research subset is cached
on GPU; E26 startup is 45.19 seconds, with zero streaming-loader wait during
training. Its original complete-pipeline profile is 22.7K origins/s; sustained
training observations are approximately 18K origins/s. Each origin contains
16 windows. E26 takes 297.13 seconds total; E27 takes 244.17 seconds, plus its
separate sub-minute readout fit. E26 development validation is approximately
four seconds per paired validation point; audit reconstruction plus controls
takes approximately seven seconds per partition.

The final disposable-copy sweep includes gathering, objectives, backward,
finite checks, clipping and optimizer work. It uses an 8,192-origin cache:

| Stage | Batch origins | Origins/s | Allocated peak GiB |
|---|---:|---:|---:|
| E26 reconstruction | 1,024 | 22,869 | 4.46 |
| E26 reconstruction | 2,048 | 20,832 | 7.69 |
| E27 frozen forecast | 1,024 | 34,658 | 3.07 |
| E27 frozen forecast | 8,192 | 35,370 | 15.69 |

E26 batches 4,096/8,192 are **unsupported** on this stack: flattened windows
exceed the efficient-attention dropout seed/offset limit of 65,535. The initial
sweep stopped on this runtime error; its partial results and failure receipt
are preserved. A new profiler records the exact limitation without changing
dropout, the model or objective. E27's frozen encoder is in evaluation mode and
does not encounter that dropout limit. All scientific checkpoint hashes were
unchanged by profiling. Profile source:
`51d1a9d9358e99dc46c1ec5009d1e3391f8eedd6`.

The full research cache raises actual E26 training peak allocation to about
15.78 GiB. Filling 96 GiB is not itself a speed objective. These short cached
profiles are **not full-dataset streaming benchmarks**, and their window counts
are not directly comparable to the earlier V5 single-window 150K/s pipeline.

Final focused suite: **23 tests passed**, including contracts, future-input
mutation, hidden-input masking, embedding-only dependency, finite gradients,
resume state, export parity, fitted-readout preservation and audit/probe paths.
Actual CPU smoke and workstation GPU training/qualification/profile paths also
completed. W&B project `mehdifaraji/bargpt-joint-v9-20261117` remotely reports
all 27 runs finished with matching checkpoint hashes. Final conclusions and
audit summaries are logged under the same run identities, without resetting
training steps.

## Next full-training configuration, for a separate decision

If scaling reconstruction is desired despite the forecast limitation, retain
E26's width-128/two-layer patch encoder, 512-dimensional learned embedding,
single affine decoder, 25% mixed masking, BF16 attention and FP32 scale/readout.
Use AdamW at 3e-4, weight decay .01, gradient clipping 1 and the measured cosine
schedule. A 50-epoch maximum schedule follows the user's prior full-training
preference, with frequent validation and rolling best/last checkpoints.

Use every eligible training record from the certified six-frame authority,
not the 90,156-origin research subset. The current Phase 1 split advertises
186,575,722 training and 47,750,007 validation windows. **Do not blindly combine
that April-2024 cutoff with this campaign's 2024 future validation:** establish
one chronological/purged, ticker-aware authority across both stages first.
Keep all validation/audit inputs out of pretraining and refit any changed
calibration on the resulting training partition only.

Start full-stream profiling near 16,384 Phase 1 windows/batch (the equivalent
of 1,024 origins), with bounded pinned prefetch and V5's 32 one-thread workers
as a candidate, not an assumed optimum. A six-frame streaming adapter and
complete I/O benchmark remain necessary; the research trainer caches its
subset and is not claimed to be that full-data launcher. Do not extrapolate
the cached timing into a full-run ETA.

For forecasting, the more justified next step is a new bounded comparison of
a smaller temporal head, explicit future-summary objectives and simple raw
summary controls with multiple seeds. E27 couples embedding size to a much
wider temporal network, so its negative result does not isolate representation
quality from forecasting capacity/optimization. **I do not recommend a multi-day
full forecasting run yet.** No new scientific training experiments were launched
after the audit; only fixed-checkpoint probes and engineering profiles followed.

## Pinned artifacts and reproduction

Training source: `009db20b34fcb7f9c6c154502a91e260e1d73d57`.
Audit/probe source: `5a4d8feec1ad28e38df8dac926496651747c353b`.
Both were tested locally, committed/pushed, then deployed as immutable snapshots
with all 934 source files SHA-verified.

Under the workstation campaign root:

- E26 checkpoint: `e26-summary-hybrid-mae/best.pt`, SHA256
  `8b858cac659d04aac2e6e2c44cb1fa50c9c3aba6f8eade234a0f8b3e47ccf5be`.
- Standalone encoder: `audit-e26-summary-hybrid-mae/window-encoder.pt`, SHA256
  `8862b31244ec4e521a67696422d71c98f838db11648c6b00bb2c2d0c781eed29`.
- Negative-transfer E27 checkpoint: `e27-frozen-summary-hybrid-forecast/best.pt`,
  SHA256 `9db97425e5d88cbef06b61c540f59de65f9fd4be15669e30d56790dd958eb928`.
  Its audit directory also contains an exactly parity-checked standalone
  embedding forecaster; retained for research, not a deployment recommendation.

The encoder export has config, frozen input calibration and encoder state only;
no reconstruction decoder is needed to encode. Reconstructing uses E26's full
checkpoint. All export comparisons passed exact parity on the evaluated examples.

Re-evaluate the pinned encoder on the workstation, choosing a **new** output
directory to preserve existing evidence:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
Set-Location D:\TradingML\codes\bargpt-joint-v9-5a4d8feec
$root='D:\TradingML\runtimes\bar_gpt\joint-v9-20261117'
& C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe -B -m research.bar_gpt.joint_v9.qualify --root $root --run e26-summary-hybrid-mae --data "$root\dataset-expanded" --output "$root\reproduction-e26-audit-01" --source-revision 5a4d8feec1ad28e38df8dac926496651747c353b --audit --phase1-data D:\TradingML\runtimes\bar_gpt\phase1-v8-learned-mae-20261117\dataset
```

`request-e26.json` records the exact fresh-training arguments, seed and source.
`request-e27.json` and `anchor-linear-initialization-e27/results.json` record
the separate fitted-readout/frozen forecasting stage. They are new scientific
experiments, not optimizer resumes. Runtime `run-final-audits.ps1`,
`run-final-probes.ps1` and `run-complete-profiles.ps1` record qualification commands;
use new output names when reproducing them.
