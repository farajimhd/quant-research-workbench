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
