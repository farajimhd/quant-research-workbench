# V21 protected single-head calibration experiments

Hypothesis: V20's range regression is partly caused by the lack of a direct
high/low range loss (the wick target needs open AND close AND an extreme).
Its activity scale may improve under a symmetric multiplicative objective.
This is a diagnostic calibration stage, not a claim that frozen encoders are
sufficient for full joint training.

Start from V20 joint unchanged-input checkpoint
fb30d0de8165fb33dfdec54901b0bc77f203d08281b729f1626e5c06aa68de13.
Freeze both encoders and the original linear head's close/body rows. Optimize
only wick rows (`range`) or wick plus activity rows (`both_log`,
`both_symmetric`). Architecture and both embeddings are unchanged; exactly one
decoder and no added input fields. Fresh AdamW with zero weight decay and
gradient masks preserves all protected rows exactly, checked at completion.

The direct range loss uses valid source high/low, predicts log(high/low), and
uses Huber in basis points. Existing wick loss retains pre-clamp gradients.
Activity arms compare log-MSE against exp(abs(log1p(pred)-log1p(target)))-1,
both with a small log-MSE and consecutive-bar change term. The symmetric
criterion treats proportional over/underestimation equally; it is NOT native
raw MAE. Targets appear only in loss/evaluation. No valid examples are clipped.

User-requested `metric` arm adds differentiable surrogates to `both_symmetric`:
ordinal BCE at the existing 0.05/0.1/0.25/0.5 percent range boundaries with a
one-basis-point log-range temperature, and BCE for activity increases/decreases
with0.1 log-unit temperature. Consecutive source-valid visible endpoints only;
true unchanged activity pairs retain reconstruction/change losses but are not
assigned an invented up/down class. Exact-zero range remains a separate known
limitation; these threshold losses cannot guarantee an exact-zero output.
Hard bin/sign accuracy is never falsely described as differentiable.
`metric_log` adds the same metric surrogates to the log-MSE activity arm,
separating activity scale loss choice from directional/range supervision.

Follow-up arms `joint_control` and `joint_metric_log` unfreeze BOTH encoders and the
one head. They use the unchanged V20 full reconstruction/retention objective;
`joint_metric_log` adds0.1 times the new range/activity/metric loss. The baseline
loss reuses the same forward tensors, avoiding duplicate attention work.
It also adds0.05 times candle-direction BCE (one-basis-point temperature,
source-valid nonflat open/close pairs), explicitly targeting the inherited
daily candle-direction regression. Unchanged candles retain reconstruction
and body losses; they are not falsely labeled up or down.
`--parent CHECKPOINT` accepts a completed hash-verified V20/V21 checkpoint for
a new scientific identity with a fresh optimizer (not an engineering resume).
Protected-row invariance applies only to the frozen-encoder calibration arms.

`joint_repaired` addresses the observed joint-control drift: retain raw-price,
body, wick and bar-anchor terms, but replace the overlapping old activity
level/change/quartic/relative terms with the successful log-calibration activity
objective. Add0.1 direct range,0.05 ordinal range,0.1 activity direction and0.5
candle direction. This is an explicit objective revision, never an engineering
resume. Removing the old activity tail term makes full-population overshoot
validation especially necessary; no raw predictions or targets are clipped.

`validate` streams the complete unchanged validation population with bounded
workers and prefetched pinned transfers. Captured inference/accumulation must
match EVERY eager metric buffer on real input; warmup contributions are cleared.
Python window counters advance once per real batch. A profile is explicitly
partial; only exact full-population coverage can produce a complete result.

Training/dev identities and uniform sampling are unchanged from V20. Hidden
bars are physically removed before both attention stages; fresh random16 masks
in three quarters of batches, all64 in one quarter. Profile eager or captured
pipeline, tiny overfit, then evaluate every500 updates. Full per-channel,
timeframe, masked/all-visible and standalone-probe metrics use the existing
V20 evaluator. Cache throughput is not a full-loader measurement.

Acceptance: inspect all groups against both V20 parent and V18 reference;
retain no candidate solely on price or average loss. Protected close/candle
and standalone bar outputs must remain unchanged apart from numeric noise.
Require improvement in range and activities without material masked or
directional regressions before a joint-training continuation and full audit.

`python -B -m research.bar_gpt.phase1_v21.experiment --output
D:/TradingML/runtimes/bar_gpt/phase1-v21-protected-20261121 --revision SHA
--steps 2000 --graphs`

The experiment refuses existing output identities; checkpoints include
optimizer and RNG. Parent/STOP/data artifacts stay intact. Each arm has a
3,000-second deadline; profiling and final evaluation fit inside60 minutes.
