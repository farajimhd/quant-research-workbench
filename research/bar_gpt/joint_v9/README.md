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
prediction inputs. Historical input-only persistence anchors may parameterize
forecast residuals, but reconstruction must recover absolute scale through the
learned embedding and cannot use those anchors as a decoder bypass.

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

Matched forecast-only and joint runs train the same affine reconstruction head
at weight 0.1. The reconstruction-weight argument controls gradients entering
the encoder: zero trains a detached reconstruction probe. This avoids comparing
a trained reconstruction decoder with an untrained one. Forecasting always sees
completed history only; its persistence anchor is historical and declared.

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
`qualify` evaluates sealed audits only with explicit `--audit`, reports full and
16-visible-bar reconstruction, and exports a standalone window encoder with an
exact parity check. Checkpoints, dataset seals, conclusions and measurement
reports live under the campaign runtime root.
