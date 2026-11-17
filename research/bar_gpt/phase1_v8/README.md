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
execution. Baseline encoders calculate no window statistics. The optional fused
encoder calculates means/coverage from supplied visible inputs only, then maps
them through a learned context projection and the common learned export layer.
Its shape branch sees centered asinh log residuals. No statistic is appended to
the latent or supplied to the decoder; every absolute output still must be
predicted. Hidden targets cannot affect those visible-input calculations.

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
error, with explicit configurable price-family weight (default one). Time and validity prediction each have auxiliary
weight .05. Full-visible and hidden-only evaluation are separate. Missing market
values cannot in general be reconstructed exactly; this is not a lossless coding
claim. Reconstruction and within-window probes are not evidence of trading value.

E02 training-only gradient diagnosis found price/activity encoder-gradient ratios
of approximately 61x all-visible and 121x masked despite comparable scalar losses.
The `price_weight=.02` ablation retains the meaningful 100*log-price residual but
reduces its family weight. Native selection metrics remain identical across
objective variants; lower weighted training loss is not an acceptance criterion.

The final fidelity ablation adds `.005 * mean((100 * log-price error)^2)`
only on all-visible batches. At 1% log error its derivative is comparable to
the weighted robust price term; at a large visible excursion its penalty grows
instead of saturating. Hidden-bar batches keep the robust objective. This is a
test of preserving visible rare events, not a license to remove such events.
All absolute values still pass through the same learned 512-value bottleneck
and single-affine decoder. No new target-dependent decoder input is introduced.

E01–E06 short profiles omitted gradient clipping and used a fixed 25% full mix;
their actual training rates include clipping and validation. E07 onward profiles
include the finite check, gradient clipping and configured visibility mix, with
a separate profile RNG. Do not compare the old microprofile rate to the corrected
end-to-end training-step rate as if the benchmark were identical. Validation
inference speed excludes CPU metric aggregation; learning records retain total
validation duration. A checkpoint metadata string inherited from V7 described
uniform rows; actual V8 sampling has always used the manifest's equal cell mass.
The V8 checkpoint writer corrects that description without changing sampling.

Pinned reproduction/finalist evaluation uses `python -B -m
research.bar_gpt.phase1_v8.finalize --root <campaign-root> --runs <run-name>
--output <new-runtime-folder> --source-revision <deployed-hash> --partition
development --nonlinear`. Use `audit` only after selecting finalists. It records
the selected checkpoint hashes before reading audit values, performs separate
all-visible/hidden-only evaluation and fixed frozen probes, then exports a
decoder-free artifact with exact encoder parity. Probe labels are within-window
summaries; the raw-summary control contains those statistics directly and is an
upper bound, not evidence of future predictive value.

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

## Bounded campaign outcome

Eight experiments completed. E07 (`e07-fused-longer-training`) was selected on
development evidence before the audit: fused width 1024, three residual blocks,
512 learned latent values, 20,000 updates, price weight .02, no squared-price
auxiliary, 75% all-visible / 25% masked batches. It has 10,443,136 encoder
parameters and a disposable 1,772,928-parameter single-affine decoder.

Audit balanced mean price error was 1.2948% all-visible and 1.5136% hidden-only
(visible-median control 2.0828%); activity log MAE was .2600 all-visible.
The frozen linear return probe improved R² from .4396 untrained to .6277;
log-price-scale MAE improved from .07982 to .001105. Range, volatility and spread
R² remained mixed; rare invalid fields and a valid extreme bid-low were not
faithfully reconstructed. This is the best supported research candidate, not
proof of complete input preservation or trading usefulness. No full run was
started. The runtime `COMPARISON.md` retains all comparisons, limitations,
checkpoint/encoder hashes, source revisions and reproduction commands.

An audit-only coverage-summary diagnostic incorrectly reused the source mask
and reported zero error. That score was removed with preserved before/after
receipts; native reconstruction, frozen probes, predicted-validity confusion,
checkpoints and selection were unchanged. The corrected finalizer excludes
source-mask coverage from decoder-summary recovery metrics.
