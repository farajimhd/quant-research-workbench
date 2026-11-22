# V20 joint hierarchical single-decoder MAE

User explicitly requested joint feature/bar attention and then clarified that
the final model must have ONE lightweight decoder. This version supersedes the
unlaunched two-decoder sketch. No earlier checkpoints or source snapshots change.

For each selected bar, feature attention4x1 produces16 learned values. A
temporal attention128x2 encoder uses those bar embeddings, original time/validity
metadata and original positions, then exports512 learned values. ONE FP32
linear512-to1152 head predicts64x18 normalized coordinates. Fixed training-only
output calibration and existing coordinate inverse recover all18 raw market
features. The model contains no Phase0 decoder, intermediate16D reconstruction
decoder, raw/reference skip connection, or hidden target metadata. The direct
head is initialized by composing the old affine maps inFP64 and storingFP32;
initial raw-metric parity is measured rather than claimed bit-identical.

The standalone16D bar embedding remains independently usable. Joint training
propagates reconstruction gradients through both attention stages. A weight1
training-only anchor to the pinned epoch19 bar embeddings discourages loss of
their existing information. It adds no input fields or decoder. A fixed legacy
bar readout is used ONLY as an explicitly labelled evaluation probe, never in
the model or training loss. It does not establish forecasting usefulness.

Hidden bars are physically removed before both attention stages.16 visible
bars are selected randomly in3/4 updates;64 in1/4, matching the current parent.
No auxiliary hidden-bar forward pass enters the encoder. Original source-valid
masks, causal metadata and split are unchanged. Reconstruction targets are
original market values, not old teacher predictions. Loss combines existing
Phase0 raw/log-coordinate objective with body, activity level/change/tail,
inverse-relative activity and wick objectives. Fixed input-reference variants
change only Phase0 activity inputs, log(1+q/r); all decoder targets remain the
same. Reference constants and input moments use training data only.

Matched arms: frozen Phase0 control, joint current inputs, joint median activity
references, joint square-root-median references. Start from preserved epoch19
Phase0 and V19 count-corrected checkpoint. Temporal/head LR1e-6; bar LR1e-7.
Fresh optimizers and new W&B identities, project `bargpt-phase01-v20-joint`.
Each arm defaults3000 updates, complete development every500, deadline3000s
reserving evaluation within60min. The original67606 training windows plus46993
sealed real training windows are cached; development11146 is unchanged. Profile
256 through8192-window batches on cloned weights, then select measured fastest.
Reported cached speed excludes data preparation and is not full-loader speed.
Only final or partial checkpoints are retained, including optimizer/RNG state.

Command: `python -B -m research.bar_gpt.phase1_v20.experiment --output
D:/TradingML/runtimes/bar_gpt/phase1-v20-joint-singlehead-20261121 --revision SHA`.
No full training launch is included: compare all old metrics and standalone bar
probe results, then qualify a finalist over the complete unchanged validation.
