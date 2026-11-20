# Phase1 reconstruction and Phase2 inheritance diagnosis

The frozen Phase1 encoder has measurable scale error and weak fine-bar reconstruction, but the Phase2 bias is not simply a carried-over fixed downward offset. Phase2 was gracefully stopped; no training was changed or restarted.

## Provenance and completed evaluations

- Phase1 epoch25 checkpoint SHA256: `879812b4d0e3bfafbdd291b292a0a6921c63e476d0ee20907b0b567acefef9aa`, pinned at `D:/TradingML/runtimes/bar_gpt/phase2-v3-v11-20261119/phase1-final-epoch025.pt`.
- Phase1 audit source: `756fbf4c0ac5c9079a7c8c4756e36766fbd46af4`. All47,750,007 original certified validation windows completed in744.33 seconds, with masked16-visible/48-hidden and all64-visible results. No nonfinite predictions in either mode. Source split/calibration hashes and model config are in the runtime authority receipt. Windows can overlap; counts are not independent market observations.
- Phase1 results: `D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118/diagnosis-full-20261120/{authority,results,summary}.json` and `report.md`.
- Phase2 stopped checkpoint step1,087,801 SHA256: `2e8e0106e0c0603aba908daaca7cb9c6bf127ed3c9845265a1ddfcc83683b9f1`. Archive: `full-p1head-lr3e5-shuffled-pass01/phase1-diagnosis-pause-1087800` under `D:/TradingML/runtimes/bar_gpt/phase2-v4-phase1-head-20261119`. Archive name uses last reported progress step; proof records the actual final optimizer step. Trainer and recorded workers exited; STOP preserved.
- Matched Phase2 audit source: `b478a4e461e30c3e782eeb7f46257527c072d25f`. All708,600 certified validation origins completed in20.01 seconds. Results/report: `D:/TradingML/runtimes/bar_gpt/phase2-v4-phase1-head-20261119/inheritance-diagnosis-20261120`.
- Four focused tests passed, including independent scalar confusion-matrix parity, masking/validity counts, bin boundaries, and a known common scale shift. Workstation smoke and both complete GPU runnable paths succeeded.

## Findings

For1s trade bars in Phase1 all-visible validation, close MAE is$0.110955; replacing only references with true visible-input references reduces it to$0.078080 (diagnostic oracle). Mean signed price-reference error is-0.026954%; mean signed last-close reconstruction error is-0.022108%. Scale recovery contributes, but is not the entire reconstruction error.

All-visible moving-only direction accuracy is59.753% for candle close versus open,61.927% for consecutive closes, and91.690% for last versus first close across the64-bar window. Masked values are56.946%,57.439%,86.755%. These are input reconstruction measures, not forecast skill. Exact flat targets are common; report also contains three-way exact direction and fixed±0.05% neutral-band direction. The latter can be dominated by the neutral class and is not independently evidence of skill.

All-visible1s trade high-low range averages0.157675% versus actual0.041283%; masked average0.075110% versus actual0.041296%. Exact magnitude-bin accuracy is6.899% all-visible /11.378% masked including exact-zero ranges, or27.790% /45.831% on nonzero target ranges. Range bins are0,(0,.05],(.05,.1],(.1,.25],(.25,.5],>.5 percent, using100*(high/low-1). Source masks and target values are unchanged. Fine range distortion is confirmed; its architectural cause has not been isolated. Different masked/all-visible target populations and visibility regimes prevent treating the gap alone as a causal ablation.

On the matched Phase2 origins, frozen Phase1 last-close reconstruction has mean signed error-0.029964%, MAE$0.159839, and61.169% below-target outputs. Phase2 first-future-close error is+0.108761%, MAE$0.209419, with7.194% downward forecasts. Signed-error correlation is0.06936. This does not support a simple inherited fixed downward bias; it does not rule out inherited information loss. These two errors concern different targets and are not an apples-to-apples MAE comparison.

Phase2's reference head differs from the true future median by-2.0763% on average, while its per-bar codes compensate. Replacing that reference alone worsens all64 trade-close MAE from$0.27694 to$7.50975. Phase2 supervises final reconstructed logs but has no separate median-reference objective: the decomposition is not uniquely identified. Thus these head values cannot be interpreted as calibrated median predictions, and this oracle is not proof of an isolated reference-head defect.

## Reproduction and next decision

Use the immutable source revisions above and `PYTHONDONTWRITEBYTECODE=1`. Run `phase1_v11.diagnose` with the pinned teacher/hash, original calibration and split, and a fresh runtime output; run `phase2_v4.diagnose_inheritance` against the preserved stopped run with a separate output. Exact commands are in each output's `process.json`. `python -B -m research.bar_gpt.phase1_v11.diagnosis_report RESULTS_JSON` regenerates the Phase1 report; append `--phase2` for matched results.

Recommended next experiments, not implemented: quantify/repair all-visible fine-bar and last-close retention in Phase1; compare explicit median/coordinate supervision against the currently unconstrained Phase2 decomposition; test reduced learning rate or parameter averaging on identical validation. Reconstruction improvement must still be checked against causal forecast baselines. Both training and scientific configuration remain unchanged and stopped pending the user's next direction.
