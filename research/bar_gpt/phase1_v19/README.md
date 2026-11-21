# V19 masked activity regression investigation

V18 completed one full pass and stopped at update22786. Its complete validation
showed masked100ms bid-size MAE increasing from41195 to11936638 (289.76x), despite
an improved aggregate quality score. Price candle direction also declined.
All parent checkpoints, datasets, receipts and STOP markers remain preserved.

First reproduce the exact failed metric over all47750007 validation windows,
same8192 batch,16 ordered workers, mask seed917 and source contract. Retain only
the32 worst windows with raw/validity/time tensors, exact masks and traversal
positions. These cases are diagnostic only: never training examples or fitted
calibration. Compare parent/stopped predictions and an explicitly labelled
untrained count-scaling ablation. Source hashes and traversal allow reproduction.

Hypothesis: the polynomial log-coordinate activity loss underweights rare raw
overshoots after expm1, while64/N slot scaling may amplify masked extrapolation.
The audit must distinguish these hypotheses from data or reduction bugs.

Four matched6000-update experiments start from the preserved stopped checkpoint,
fresh AdamW1e-5, seed559, original training/development subsets and fixed teacher.
`control` retains V18 exactly. `relative01` and `relative1` add weights0.1/1 of
Huber(beta0.1) on (prediction-target)/(1+target), averaged equally over valid
activity channels and source-valid bars. This is computed as expm1 of the log
ratio in FP64; no valid target or prediction ceiling is introduced. It includes
hidden bars with full weight. Existing log-level gradients still correct negative
pre-clamp coordinates. Other objectives, architecture, masking and256D embedding
remain unchanged. A changed objective has a new W&B identity.

No raw inputs, references, directions or statistics are appended to embeddings.
Targets enter loss/evaluation only. Calibration uses the original training subset;
no validation outlier is trained on. Each arm is bounded to60minutes including
its tiny-fit gate and evaluation. Selection must retain price/body quality and
reduce native tail errors, then pass full-population validation before full training.

The full forensic scan reproduced MAE11936637.613190947, matching the failed
validation reduction. Its32 worst windows contribute99.9251% of total error.
Worst target51200 was predicted as4.848e15, versus9.031e12 by the pre-full parent.
On the same cached visible inputs, removing count scaling without retraining
reduced the stopped model prediction to951898; all-visible prediction355820.
This confirms amplification, not accuracy of an untrained scaling change.
`unscaled_relative01` is the fourth matched arm: same relative01 objective,
but no64/N multiplication of positional slots. Both architecture state and
loss identity are recorded; checkpoint readers restore the count-scaling flag.
Known failure cases are evaluation-only stress tests and are explicitly labelled
as such. They are neither pristine held-out evidence nor fitted training targets.

Canonical input check: all32 retained failure bars were traced back through the
ordered loader to exact sealed shard rows; payload/window hashes matched. Their
bid/ask closing prices and displayed sizes matched the latest causal canonical
quote in `market_sip_compact.events_2025`, with zero mismatches across nine tickers.
The worst GNLN bar has canonical bid0.0129/size51200 and ask0.0131/size9700.
This is targeted source confirmation, not a fresh audit of every dataset field
or quote. `source_audit` records its precise scope and evidence under the runtime.

The four continuation arms failed qualification: relative1 retained catastrophic
masked stress outputs (maximum4.96e11), while changing count scaling on the
trained checkpoint destroyed development price reconstruction (1s close MAE27356).
Do not resume that checkpoint unchanged or promote any of these repairs.

Next matched experiments use scratch initialization,15000 updates,AdamW1e-4,
and relative01/relative1 objectives with unscaled or centered-slot architectures.
Centered slots combine a learned CLS projection and a learned projection of
position-aligned token deviations from their visible mean, by addition into the
same256D embedding. The decoder remains the existing single linear layer.
No input reference, metadata or raw feature bypasses that embedding. The fixed
teacher and its calibration remain unchanged. Architecture and initialization
are separately recorded; these are new scientific identities, never resumes.

Promotion requires complete full-population validation and comparison against
the preserved pre-full candidate and stopped checkpoint across all18 channels,
both visibility modes, all timeframes, price/body and activity directions.
Aggregate improvement cannot excuse worsened working groups. Known32 cases are
diagnostic only. These scratch experiments may be rejected even if their tail
errors improve. Full training remains stopped until qualification succeeds.

Additional controlled ablation `mean_filled`: missing temporal states receive
the mean of visible encoded states before the learned slot projection, with no
count multiplier. This preserves the parent's all-visible computation exactly
(tested at zero tolerance), allowing a matched continuation rather than requiring
scratch learning. It does not reveal missing inputs or add embedding components.
Run from the preserved parent with separate identity, unchanged decoder, and
control/relative01/relative1 losses to separate the architecture and objective.

Scratch unscaled relative01 completed15000 updates but was rejected: activity
direction gains came with severe price errors. Relative activity loss alone
does not protect prices. `relative_all` therefore adds an exact price-relative
Huber term (weight1,beta0.1) on exp(predicted_log-target_log)-1 to relative01.
It uses all12 valid price channels, including original quote pairing, and no
ceiling or target substitution. Test identity covers very small positive prices.
The mean-filled continuation compares control,relative01,relative1,relative_all
from the pre-full parent (better retained candle direction),6000 updates1e-5.

The V19 full runner is prepared but must not launch before full qualification.
It pins the candidate, full-validation result and validation checkpoint identity;
preserves architecture flags on engineering resume and encoder export; retains
optimizer/RNG/cursor and the original worker-count traversal. Native CPU tests
check frozen-teacher gradients, hidden-input isolation and exact resumed updates.
Full GPU graph/gradient parity and pipeline profiles are required before launch.
All group regressions remain reported. Emergency guards are tightened from10x
raw error to1.10x, or a direction drop exceeding1percentage point (two successive
development checks; immediate after full validation). These are stop thresholds,
not permission to accept smaller regressions. Promotion remains a separate gate.

Coverage diagnosis: original training100ms has only4 windows with bid<0.10,
all below0.01; zero windows cover bid0.01--0.10. `training_pool` builds a
deterministic CPU-only supplement from the sealed TRAIN loader index,1024
entries per timeframe, four threads,20minute preparation deadline. Each selected
payload/receipt is hash-verified; partition0 and cutoff boundaries are asserted.
Candidates include price-band, activity-tail and uniform rows. Exact existing
training identities and same-security/timeframe overlapping supplement windows
are rejected with counts; inputs and source-valid values are copied unchanged.
Manifest records identities, weights, hashes, coverage and selection probabilities.
The supplement never reads validation/audit rows. Latent normalization remains
frozen; activity loss calibration uses combined training only. Development stays
the original11146 windows. Equal selected-window weighting is deliberately not
population-unbiased and is not used for full-validation metrics.

The supplement completed in34.68s:46993 nonoverlapping windows from6144
verified entries, including31 independent100ms windows in the previously absent
0.01--0.10 bid band. All four mean-filled6000-update continuations were rejected:
close MAE1.03--1.13 versus0.405, despite candle direction92.7--93.6 percent.
The next matched coverage arms keep the original scaled architecture, use the
supplement,15000 updates and lower LR3e-6 (control,relative01,relative_all).
These isolate loss effects on the expanded pool; coverage versus LR is not a
pure ablation relative to earlier runs.

`control_variate` is a separate targeted pooling hypothesis: for visible states
h, fill hidden positions with visible mean(h), and visible positions with
mean(h)+(64/N)*(h-mean(h)). This retains count correction on variations but removes
random common-component amplification. All-visible computation remains exactly
the parent (CPU zero-tolerance test). No added parameters, raw decoder inputs or
embedding fields. An initial zero-update evaluation is labelled as such; it
cannot be described as trained evidence. The same inference architecture must
be retained when exporting the encoder or resuming.

Optional capacity ablation `--embedding 512` expands learned readout rows and
decoder input columns only. The existing256D learned projection/decoder weights
are preserved; extra readout rows use seeded initialization and extra decoder
columns start at zero. Tests verify initial prediction parity at16/64 visibility
and nonzero gradients into the new decoder columns. Nothing raw or deterministic
is appended. It remains one linear512-to1024 decoder. Compare against256 with
the same parent, expanded training pool, LR3e-6,15000 updates and relative_all.
This tests capacity, not a claim that increasing capacity fixes rare-scale tails.

`scale_augmentation` is a separate train-only robustness hypothesis, not new
historical evidence: each real training window has one synthetic copy with
prices multiplied by2**k and volumes/displayed sizes divided by2**k, k sampled
uniformly from{-8..-1,1..8}. Trade/quote counts, time, validity, candle signs,
activity-change signs and ordering stay unchanged. Every valid numerical change
must invert exactly or preparation fails; no ceilings/clipping. Original shards
and validation/audit sets are unchanged. Synthetic identities retain source row,
source hash and exponent. Its manifest labels synthetic counts and weighting.
The frozen Phase0 teacher must be checked on these augmented values before
training; poor teacher information retention would invalidate this approach.

Aggressive augmentation was rejected before Phase1 training: on768 paired
training windows, frozen Phase0 1s candle direction fell96.19% to85.21%.
Separating price-only and quantity-only shifts still gave86.19% and87.66%.
The augmented artifacts remain labelled and preserved, but no candidate trains
on them. This is a measured teacher sensitivity, not evidence of corrupted data.

The512D capacity arm on real data improved development1s close MAE to0.31385,
candle direction92.37%, and activity direction; masked stress max remained2.394e9.
Applying mean-filled inference without updates retained identical all-visible
metrics and reduced masked stress max to1.319e6, but masked close MAE rose0.8521.
The next `mask_adapter` adds a zero-initialized learned512-to512 residual inside
the encoder, gated by1-N/64; it is bypassed entirely at N=64. Decoder input is
still exactly512 learned values. `--adapter-warmup` freezes the existing encoder
and decoder, trains only the correction on16 random bars per batch, and uses a
16-visible tiny-fit gate. This protects all-visible outputs exactly during warmup
(tested after an optimizer update). Full training, only after qualification,
unfreezes the full model; this warmup is not a claim that its frozen all-visible
representation improved. Native profiling handles inactive-branch gradients.

The6000-update adapter warmup succeeded on development: masked1s close MAE
0.37852 versus0.71702 pre-full V18, masked stress max1.168e6, and all-visible
outputs exactly unchanged from512D parent. Wider comparison still rejects full
promotion: parent512D daily bid/ask-count MAE is about31% worse than V18.
Next matched6000-update arms unfreeze the whole model at1e-6, equal-frame
sampling, unchanged real pool: relative_all versus relative_visible_symmetric.
The latter uses exp(abs(log((1+pred)/(1+target))))-1 on visible activity bars,
retaining asymmetric overshoot protection on hidden bars. This tests whether
the original relative penalty's bounded underprediction cost biases counts low.
No synthetic augmentation is used. All512 fields remain learned; decoder unchanged.

Comparison now also includes MAPE and bin/magnitude agreement, not only raw/log
MAE and direction. Emergency direction guards use observed/all-visible metrics;
masked direction near chance remains reported rather than being treated as a
working reconstruction signal. All masked raw/MAPE regressions remain guarded.
