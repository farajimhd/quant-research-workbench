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

`encoders` exports standalone bar/window encoders from a hash-verified completed
candidate. Neither artifact contains decoder weights. Their input contract is
the existing prepared27 values (log prices,log1p activities,nine time fields)
and27 validity flags; window input has64 bars, optional visible positions.
The bar export returns16D independently per bar; the window export returns512D.
CPU tests cover exact export parity and confirm no decoder in either state.

Optional matched ablation `--decoder-visible-only` trains the one output head
only on64-visible updates; BOTH attention encoders still receive gradients on
16-visible MAE updates. Hypothesis: unknown-bar activity targets bias shared
output calibration downward, harming all-visible reconstruction. Forward
architecture, parameters, loss, masks and data are unchanged. AdamW must skip
the inactive decoder even after it has optimizer momentum; tested explicitly.
This is an experimental gradient policy, not a claim that standard MAE requires
it. It is recorded in the scientific contract and must survive any full resume.

Optional `--graphs` captures joint forward/backward separately for16/64 visibility.
Each new capture must match eager loss, components, norm and every parameter
gradient on identical tensors before optimization. Optimizer steps remain outside
capture; all raw/valid/time/target/position buffers update on every replay. No
scientific precision change. Profile reports eager/captured qualification and
memory separately; cached engineering speed is still not full-loader throughput.
