# BarGPT V8: entirely learned reconstruction embeddings

The user rejected V7's deterministic scale/coverage slots. V7 was self-contained
but absolute scale was copied, not learned. Its reconstruction does not establish
learned recovery of that information. Its full run is stopped and must not resume
without new authorization; the final checkpoint and user STOP are retained.

V8 has a strict learned bottleneck: every exported value is a learned projection
of encoder activations. There is no concatenation of input means, references,
validity fractions, quote gaps, or target metadata into the exported embedding.
`decode(z)` takes only that embedding and fixed training-calibration buffers.
The encoder-only artifact contains no decoder parameters. A negative shortcut
test zeros all encoder weights and requires identical embeddings/reconstructions
for different inputs.

Inputs are 64 bars with 18 source-valid market channels and the existing nine
causal UTC/timeframe features. Prices use log, activity uses log1p; fixed global
channel mean/std are fitted only on the frozen training subset. Validity bits
are explicit encoder inputs. Hidden bars are physically removed before encoder
execution; no full-window statistics are calculated for the encoder or decoder.

An MLP control and attention/CLS encoder are experimental alternatives. One affine
decoder predicts 18 market coordinates, nine time coordinates and 27 validity
logits per bar. Direct price outputs or optional fixed OHLC/paired-quote algebra
are alternatives; in every case all coordinates, including absolute levels,
must be predicted from the learned latent. Activity output uses a learned square
root of log1p activity, squared before expm1, ensuring nonnegative outputs without
clipping source values. Fixed coordinate calibration is training-only. All
native reconstruction metrics use original source-valid masks, never predicted
validity to exclude difficult observations.

The price objective is Huber on `100 * log-price error`; activity uses log1p
error, with equal family means. Time and validity prediction each have auxiliary
weight .05. Full-visible and hidden-only evaluation are separate. Missing market
values cannot in general be reconstructed exactly; this is not a lossless coding
claim. Reconstruction and within-window probes are not evidence of trading value.

Campaign runtime: `D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117`.
At most eight experiments and eight hours, with each experiment strictly below
60 minutes including preparation/profile/training/evaluation. Dataset preparation
is measured separately. Frozen training/development arrays are reused from the
previous certified subsets. Fresh audit groups exclude both prior campaigns'
audit/development groups. Because V7 already aggregate-evaluated the full original
validation population, this is newly sealed audit sampling, not a pristine
external test. The audit remains unopened for model evaluation until finalists
are selected from development evidence.

Training uses equal timeframe/price-band cell mass. Calibration and metrics use
unweighted selected valid observations; no population-unbiased claim is made.
Original split hashes, dependencies, row identities, nonoverlap and chronological
boundaries remain required. No historical event reads or source-data changes.

Run one experiment through `python -B -m research.bar_gpt.phase1_v8.launch
--root <runtime> --name <unique-experiment> -- --source-revision <deployed-hash>
--hypothesis <hypothesis> ...`. Set `PYTHONDONTWRITEBYTECODE=1`. The controller
checks finite campaign limits, owns the child process, and gives it a bounded
graceful shutdown before the strict wall ceiling. No recurring automation.
New W&B project: `mehdifaraji/bargpt-v8-learned-mae-20261117`.
