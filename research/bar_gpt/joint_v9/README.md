# Joint V9: choose representations by future-task generalization

The primary criterion is leakage-safe future-bar prediction; held-out input
reconstruction is a supporting metric and a hypothesis about forecast
generalization, not a substitute for measuring it. The preceding V8 MLP is a
research control, not an assumed generalization winner. No prior training run
or human STOP is resumed by this version.

The actual Phase 2 input is sixteen 64-bar windows: 8×1s, 4×10s, 2×1m, 1×1h,
1×daily. Labels are the next 64 observed 1s bars inside the same source block.
Every completed context end must be <= origin; every label end must be > origin.
Labels, their masks, timestamps and any future-derived statistics are never
prediction inputs. Historical input-only persistence anchors are conditional
controls only. Recommendation candidates must recover absolute scale through
learned embeddings for both reconstruction and forecasting. Neither decoder
may receive raw reference values outside the exported representation.

The dataset builder uses the sealed all-published Phase 2 certificate and hashes
every selected source payload through the existing reader. It preserves source
values, masks, session split basis and chronology. Duplicate ticker/session
publications are grouped before sampling. Up to four nonoverlapping future
supports are selected per session; historical context can overlap within a
partition. Train ends before 2024; development contexts begin in 2024; audit
contexts begin in 2025. A stable SHA-based ticker holdout is excluded from all
training years. Original 2026 validation is retained separately and has limited
ticker coverage. These are sealed internal evaluations, not pristine external
tests of the entire historical research process.

Architecture families to compare include shared temporal-patch attention,
hierarchical window/timeframe attention, variable-token attention, temporal
convolutions and appropriate simple controls. Explicit window position,
timeframe and input age relative to origin prevent treating ordered windows as
an unordered set. Entirely past context may attend bidirectionally; absence of
future inputs, rather than an unnecessary triangular mask over known history,
enforces origin-level forecasting causality. Autoregressive and direct
multi-horizon prediction remain experimental alternatives.

Motivating primary sources (not evidence for performance on these markets):
- PatchTST: https://arxiv.org/abs/2211.14730
- iTransformer: https://arxiv.org/abs/2310.06625
- TSMixer: https://arxiv.org/abs/2303.06053

Runtime authority: `D:/TradingML/runtimes/bar_gpt/joint-v9-20261117` on each host.
New source snapshots are immutable, hash-verified and deployed only after laptop
tests, commit and push. Generated data, profiles, receipts, reports and bounded
checkpoints remain in runtime. One GPU experiment at a time, with explicit
per-experiment deadlines, frequent validation and graceful checkpointing.
No full-data multi-day run or recurring scheduled automation is implicit.

## Screening and qualification protocol

The initial dataset has 9,600 training origins from 2,400 independent sessions.
Development and ticker-held-out development each contain 960 origins. A nested
training expansion keeps every original training record byte-exact and copies
all holdout identities and payloads unchanged. Expanded-data runs receive a new
data/experiment identity. Calibration can be refit on training only, or frozen
from an explicitly named original training calibration for controlled expansion
comparisons. The latter verifies that the original training dataset is retained
byte-exact and records the calibration manifest hash; no validation statistics
are used.

Forecast evaluation includes last-bar persistence and stronger historical-window
log-activity mean/median controls. A high R-squared for future activity change
from the latest bar can mostly reflect the known latest-bar value; therefore
probes also report actual future activity levels and changes from historical
means, with causal-control errors. These labels are explicit summaries of the
certified future bars, not invented trading outcomes.

Matched forecast-only and joint runs train the same affine reconstruction head
at weight 0.1. The reconstruction-weight argument controls gradients entering
the encoder: zero trains a detached reconstruction probe. This avoids comparing
a trained reconstruction decoder with an untrained one. Forecasting always sees
completed history only. Historical-anchor runs are explicitly conditional
forecasting controls, not evidence that an embedding preserves absolute scale.
For a recommendation, the stricter `--anchor-mode learned` path must decode its
anchor from the learned latest-window embedding. Its Phase 2 method accepts only
the sixteen embeddings and causal ages and rejects external anchor inputs.

Optional masked reconstruction uses a separate encoder pass: selected windows
retain sixteen random bars, with all features and metadata hidden at other
positions. Full-history forecasting inputs remain intact. The reconstruction
head still receives only learned latents; it never receives input references.
Optional future-summary supervision predicts future realized volatility, range,
and six activity changes from the joint embedding. It addresses information lost
under the per-bar point-prediction objective, without introducing future inputs.

The first six short screenings favor patch attention on forecasting, while
convolution reconstructs prices better. Forecast improvements were in activity;
none established price prediction skill over persistence. Initial frozen probes
also found stronger volatility/range performance from simple raw summaries than
the learned joint embedding. These observations motivate longer, broader-data,
masked-reconstruction and auxiliary-summary comparisons; they are not a final
architecture recommendation.

`probes` compares identical training-only standardized ridge readouts of raw
historical summaries, frozen joint embeddings, and concatenated frozen window
embeddings, including untrained controls. The last comparison isolates whether
the Phase 1 embeddings retain future-useful information before Phase 2 mixing.
Feature dimensions are reported rather than treated as parameter-matched.
CPU FP32 probes are screening diagnostics; final deployment-precision probes
use `--device cuda --source-revision <immutable revision>` under the same GPU
ownership guard. They also test within-window scale and shape retention, which
distinguishes missing information from an inadequately fitted reconstruction head.
`qualify` evaluates sealed audits only with explicit `--audit`, reports full and
16-visible-bar reconstruction, and exports a standalone window encoder with an
exact parity check. Checkpoints, dataset seals, conclusions and measurement
reports live under the campaign runtime root.

## Input and export dimensions

Each bar has 18 log/log1p market values and nine existing time features. Appending
their 27 validity flags gives 54 token-input channels. An eight-bar patch has
432 inputs and is projected to the attention width. This is a learned patch
projection, not additional source data. A separate FP32 learned context path
receives 27 window means and 27 coverage fractions; the optional last-context
ablation also receives the latest visible market values and their 18 flags
(90 context inputs total). Hidden-bar values cannot enter either path.

The export projection produces the fixed-size learned window embedding from
the context path and either the attention summary token or all patch tokens.
No coordinates are copied into reserved embedding slots. One affine decoder
predicts 64x54 outputs: 18 structured market coordinates, nine time features,
and 27 source-valid logits. Fixed training-only inverse transforms recover raw
market units. The reconstruction decoder can be discarded.

The standalone `EmbeddingForecaster` consumes sixteen window embeddings and
their causal ages. It contains ordered multiscale attention, a lightweight
future head, and an 18-coordinate learned anchor head. It contains neither the
window encoder nor the Phase 1 reconstruction decoder, and accepts no raw
historical bar or external reference. Export checks exact prediction parity.
# Reconstruction-first comparison

`--forecast-weight 0 --reconstruction-weight 1` trains only the window
encoder and its single affine reconstruction decoder. Future targets and the
temporal forecasting network are not used by this training loss. Checkpoint
selection uses development reconstruction; its untrained forecasting outputs
are diagnostic only. Masked reconstruction remains independently configurable.

For a separate frozen-encoder forecasting experiment, use
`--initial-checkpoint <pretext-best.pt> --freeze-encoder` with the same model,
calibration and data configuration. This creates a new optimizer and schedule;
it is not an engineering resume. The encoder runs in evaluation mode during
training, receives no gradients, and its final state is checked byte-exactly
against initialization. The forecasting and learned anchor readouts can train.
Only this second stage and frozen probes establish future-task performance.

`--context-embedding 128` optionally separates a learned scale/context readout
from the learned attention-shape readout within the same fixed-size embedding.
Both paths contain trainable projections; neither copies input references into
latent slots. Zeroing the encoder makes the entire embedding zero and forecasts
input-independent. The reconstruction decoder remains one affine layer.

`--horizon-queries` replaces the single Phase 2 pooled query with 64 learned,
fixed horizon queries and a shared 18-channel readout. Queries contain no
future values, validity flags or timestamps. Attention operates on past window
embeddings, causal ages and these fixed queries only; no autoregressive target
feeding is used. Phase 1 and its reconstruction decoder are unchanged. Mean
query features are used only for the compact Phase 2 probe diagnostic.

`--activity-basis` uses square-root-of-log1p activity coordinates before
visible-context centering, matching the squared activity readout. The visible
mean in these coordinates and the visible latest values enter learned context
projections; original log-level summaries remain available too. Prices retain
their log-relative shape coordinates. This tests a confirmed expressivity
restriction of a purely linear log-level context path with a squared decoder;
performance gains must still be established experimentally. No true mean or
reference is passed to either decoder.
