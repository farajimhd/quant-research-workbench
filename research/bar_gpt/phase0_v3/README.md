# Phase0 V3 activity input reference ablation

User-authorized investigation of errors inherited by Phase1. Preserve the
epoch19 Phase0 teacher and every Phase1 checkpoint. This is a new scientific
identity, not a resume or replacement of the frozen teacher.

Compare matched4000-update continuations: unchanged input control, fixed positive
training median reference, and square root of that median. Six activity inputs
become log(1+q/r), with mean/std fitted on valid training bar exposures including
zeros. References are fixed per channel, not per example or timeframe; no
validation calibration, clipping, new embedding fields or decoder side inputs.
The first ablation changes inputs only. Existing18 output coordinates, inverse,
source masks, loss, attention4x1 encoder,16D embedding and linear decoder remain
unchanged. A changed Phase0 embedding is not automatically compatible with old
Phase1 weights.

Use the original frozen training subset plus the sealed V19 real training
supplement; development remains the same11146 windows. Every bar in each
selected window is independent to the encoder. Samples are uniform training
windows; overlaps/exposures are not claimed unique. Positive medians choose
references, while input moments include all valid zeros. FP64 transform
roundtrips include zero, tiny and large values. No raw activity value is lost
from the reconstruction target.

Each arm has a tiny-fit gate and independent identity (actual training
deadline3000s reserves evaluation within60minutes), periodic complete development
evaluation of all18 raw features, candle/range metrics and six activity
direction/bias metrics, bounded final/partial checkpoint, optimizer/RNG state,
and online W&B project `bargpt-phase0-v3-activity-reference`. Default4000updates,
1024windows/batch,AdamW1e-6. Cached speed excludes source preparation and is not
advertised as full-pipeline speed. No full training is launched by this module.

Reproduce with `python -B -m research.bar_gpt.phase0_v3.experiment --output
D:/TradingML/runtimes/bar_gpt/phase0-v3-input-reference-20261121 --revision SHA`.
