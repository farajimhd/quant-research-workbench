# V26 final occupancy report

This standalone package is outside the optimizer's hashed `v26/` package. It never updates optimizer code, pointers, checkpoints, selection, scores, RNG or receipts. Run it from a separate committed immutable deployment; the current optimizer does not need a restart or migration.

```
python -B -m research.vectorized_backtest.posthoc.v26.run --check-only
python -B -m research.vectorized_backtest.posthoc.v26.run
```

The default output is `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v26/diagnostics/post-phase2-occupancy`. `--scheduled` disables only `Codex-V26-Final-Occupancy` after success. A scheduled check while the optimizer is alive reads only its pointer/process identity; it imports no engine, NumPy or Torch and creates no workers.

## Completion and candidates

Analysis requires successful controller exit, ownership release, the exact generation-50 checkpoint state hash, 24 training and 12 validation receipt hashes, both panel hashes and display-only validation seals. It uses the entire training-selected final validation shortlist, up to512 candidates. The checkpoint binds the candidate identities, original order, payloads, training ranks and profits. Validation results never determine candidate inclusion.

## Exact occupancy

Closed-position intervals encode binary occupancy on the certified one-second replay clock. Intervals use `[entry, exit)` and a one-second minimum for zero-duration records, matching V26's existing behavior ledger. Listings and sessions have separate coordinate namespaces. Touching intervals merge. Empty exposure is undefined, not automatically dissimilar. Quantity and intermediate add/reduce decisions are not weights.

Identical interval patterns deduplicate within each session. Batched prefix-integral queries calculate only the upper triangle. Both partner rows and endpoint chunks are bounded (default32 MiB query scratch); no candidate×ticker×second grid or pair×second tensor is allocated. Intersections and occupied durations sum across sessions before calculating Jaccard, cosine and directional coverage. Per-session matrices and per-split seals support exact restart.

## Training reuse and validation traces

Training clocks are reused directly. Existing validation clocks are also reused when available. Missing validation clocks require a separate trace replay only after completion, using the completed optimizer's immutable code, original complete shortlist/order, original batch size and paired-session replay widths. One worker preserves the original numerical rule shapes. All existing saved metric fields except the operational result-cache reuse flag must match exactly; data/execution/population identities must also match. A mismatch fails closed. Only the new trace clocks are added, in the analysis output, never the campaign.

The worker preserves the qualified same-platform Triton driver cache setup. It uses two host-session slots, no speculative prefetch, the128 GiB host reserve plus32 GiB admission allowance, and yields when an optimizer becomes active. Scheduled checks retry deferred work; no recurring Codex monitor is enabled.

Outputs: `result.json`, `report.html`, `training.npz`, `validation.npz`, nearest-neighbor/contained-pair summaries and candidate/provenance seals. Full matrices preserve the frozen candidate order; undefined entries are NaN in NPZ and omitted from pair lists.
