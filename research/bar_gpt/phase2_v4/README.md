# Phase2 V4: identical Phase1 decoder heads for future bars

User requested a separate copy of V3 with the actual Phase1 V11 decoder heads,
and authorized fixes to problematic training and scheduling. V3 checkpoints,
logs, identities and source snapshots remain independent.

The context encoder remains two attention layers,width256,4heads,FF1024 and a
256-dimensional export. Freeze the same completed Phase1 epoch25 encoder.
The decoder exactly reuses `phase1_v11.model.Autoencoder.decode`: linear256->64x54
readout, separate32->9 reference head, price-coordinate factor100, structured
OHLC/quote inverse and FP64 raw-unit inversion. Its weights initialize from that
same Phase1 checkpoint. Both heads receive only the exported256-vector; the
reference head reads its last32 dimensions. Future references are never inputs.

The54 outputs per bar are18 market coordinates,9 time fields and27 validity
logits, exactly as Phase1. This forecast experiment supervises all18 future
market features using original source masks. It retains the V3 raw-log forecast
objective (price1000x log error,activitylog1p,equal family SmoothL1). Auxiliary
time/validity outputs are retained for head identity but do not enter the loss.
There is no separate future-median auxiliary loss: references learn through the
inverse-transformed forecast objective, not a claim of calibrated future medians.
Targets remain the next64 OBSERVED1s bars. This changes prediction targets, not
the decoder's equations. All64 horizon metrics and causal controls are retained.

Two additional changes address the diagnosed training instability:

- Fixed AdamW learning rate3e-5 after1000updates of warmup from3e-6. It does not
  wait for the multi-billion-origin traversal to decay. Validate every5000updates.
- Deterministically shuffle complete training sessions within each certified
  release root (seed17041+rootindex). Keep every session/block/eligible origin,
  all within-window ordering and the original validation population/order.
  Pin Polars version and actual traversal hashes. Resume uses the shuffled
  publication/block/origin cursor. Batches still contain overlapping windows;
  session shuffling reduces long ticker runs but is not independent-window sampling.

This is a combined head/initialization/order/LR experiment, not a transformation-only
ablation. It is not yet evidence of better forecasting. One full pass initially,
rolling last/best checkpoints,patience24 full validations. W&B remains
`mehdifaraji/bargpt-phase2` with a NEW run identity. Qualified engineering settings
start from batch4096,4CPU I/O/4 preparation workers,tokenbatch1024,8GiB prefetch,
4GiB session cache,BF16 attention/FP32 heads,fused optimizer,CUDA graph steps.

Entry points: `python -B -m research.bar_gpt.phase2_v4.run_train --mode profile`
and `--mode train --execute`, with explicit release,teacher SHA,calibration,
immutable source revision and runtime output. Detached controller:`phase2_v4.launch`.
Rich monitor:`phase2_v4.panel --output RUN`. Commands and measured profiles belong
under the workstation runtime root, not source. First qualify exact head parity,
future shapes,masks,gradient/resume behavior,tiny fitting and full pipeline speed.

Initial launch: source `95cad7fe0d4cdf1735d1b1c5de85467b632a5971`, run
`full-p1head-lr3e5-shuffled-pass01`, W&B `mehdifaraji/bargpt-phase2/gh6kl6tc`.
Runtime:`D:/TradingML/runtimes/bar_gpt/phase2-v4-phase1-head-20261119` holds
`training-command.txt`,`start-full.ps1`,deployment proofs and profile results.
The256-session profile covered782733 eligible origins at49920/s including startup;
cached compute peaked near350K/s at batch4096. Tiny32-example loss1.515->0.789
after400updates. Exact head parity,mask/gradient,CUDA resume,fixed schedule and
session-population/order tests passed. Real train/validation/checkpoint integration
completed13updates. This is qualification, not proof of forecast superiority.
V3 stopped gracefully at85656; its final checkpoint,logs and STOP are retained.
