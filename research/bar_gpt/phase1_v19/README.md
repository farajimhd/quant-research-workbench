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
