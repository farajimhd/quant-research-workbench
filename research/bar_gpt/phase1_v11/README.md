# V11 median-centered Transformer MAE

Experimental, not yet qualified for full training. Preserve all older runs and STOP files.

Each family transforms OHLC to `s=log(C), d=log(O/C), u=log(H/max(O,C)), l=log(min(O,C)/L)` and activity to log1p. Median-center s and both activity channels using only the 16 randomly visible bars. Nine references and their availability flags enter one reference token. Missing quote values are completed deterministically within OHLC/bid-ask order solely to construct coordinates; original validity remains authoritative for raw losses and metrics. No difficult valid observation is clipped or dropped.

Bar tokens contain 18 centered coordinates, nine canonical time fields and 27 source validity flags. Price coordinates are multiplied by 100 and market inputs conditioned with asinh. Training-only fixed statistics normalize time and the reference token. Four learned pooling tokens traverse eight width384 normalized-QK Transformer layers (12 heads); their concatenation projects to one 1024-dimensional embedding. There is no separate scale-summary bypass. Only this embedding feeds the affine bar decoder and nine-reference head. Readout/inversion use FP32/FP64. Predicted nonnegative wick/activity parameterization and quote ordering constrain predictions; never targets.

The raw-log price objective uses 100× log error, separately weighted price-reference recovery, activity-log reconstruction, and small coordinate/time/validity auxiliaries. Hidden48 reconstruction trains the model; all-visible reconstruction is evaluated separately. This intentionally changes V10's architecture and objective, so the initial comparison is not a transformation-only ablation. Oracle-reference metrics are diagnostic only. Recovering randomly hidden market fluctuations exactly is not generally possible.

Frozen dataset: `D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset`. Hash verification precedes use. Calibration uses selected training observations only; no sampling weights in calibration, uniform selected examples in training/metrics, with balanced timeframe/channel summaries. Original chronological/group partitions remain fixed. Audit is reserved for finalists. Full-data training requires separate qualification, streaming profiles and approval gates already authorized by the user; this finite runner does not automatically launch it.

All output goes under `D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118`. Run on a committed/pushed, hash-verified workstation snapshot, with `PYTHONDONTWRITEBYTECODE=1`:

```text
python -B -m research.bar_gpt.phase1_v11.run prepare --output D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118/calibration.json
python -B -m research.bar_gpt.phase1_v11.launch --output D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118/e01 -- --source-revision FULL_GIT_HASH --calibration D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118/calibration.json --minutes 18 --train-minutes 12
python -B -m research.bar_gpt.phase1_v11.panel --output D:/TradingML/runtimes/bar_gpt/phase1-v11-median-20261118/e01
```

Every experiment first runs tiny overfit and GPU-cache batch profiles, then time-bounded training with repeated development checks, bounded best/last checkpoints, and complete final raw reconstruction evaluation. W&B project `mehdifaraji/bargpt-phase1-v11-median`. Incomplete evaluation is a failure, never an acceptance. Maximum eight experiments/eight campaign hours, each ≤60 minutes. Cached compute profiles are not full-data loader throughput claims. No scheduled automation.

The `--readout include_reference` ablation replaces the fourth latent readout slot with the encoded reference token **after all eight Transformer layers**, retaining the same token count, parameter count, embedding size and decoder. It tests scale retention and token-count sensitivity without giving the decoder any external reference. `--probes` adds frozen within-window information probes against an untrained encoder and raw-summary information ceiling. `stream_profile` measures the complete certified loader→GPU path with bounded concurrency; it discards updates and never mutates its source checkpoint.
