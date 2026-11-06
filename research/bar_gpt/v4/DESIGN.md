# BarGPT v4 pretraining and deferred trading supervision

Status: design proposal, 2026-11-06. No model implementation or training is authorized by this note. The immediate priority is standalone market pretraining; contrastive trading supervision is deferred. Existing v3 behavior remains authoritative for v3.

## Standalone pretraining proposal

- Shared causal Transformer over individual ticker/timeframe streams. Use 1s, 15s, and 1m views with 128 completed bars per view. Encode views separately with shared weights and timeframe metadata; downstream modeling will combine their embeddings across tickers.
- Retain the previously discussed 26 numerical channels: six trade fields, six bid fields, six ask fields, four condition counts, and four time fields. Exact field names, size aggregation, condition definitions, transformations, and missingness semantics must be agreed and source-verified before implementation. Do not silently substitute available shard fields.
- Keep availability, padding, identity, timestamps, and timeframe metadata separate. No technical indicators, future labels, position state, or account state enter pretraining.
- Proposed starting model: six pre-norm Transformer blocks, width 256, eight attention heads, feed-forward width 1024. These are engineering starting values, not benchmark conclusions.
- Return every causal hidden state, shaped [batch, 128, 256] per view. The final state summarizes its available prefix; all states remain available to the later model.
- Use disposable direct heads to predict the next completed bar's trade, bid, and ask observations, plus observation availability. Do not reconstruct the already-visible current bar or introduce a bidirectional MAE decoder in the initial experiment.
- Proposed numerical treatment: scale price levels using a causal session anchor fixed before the sequence, use log1p for nonnegative size/count fields, and fit any standardization on training data only. These are proposed transformations of direct inputs, not additional feature channels. Review their exact definitions first; input scaling and next-bar target scaling need separate contracts.
- Predict future price geometry relative to the current observed price for stable targets; use robust regression for numerical targets and binary classification for future observation availability. Future price/size/count losses are masked when the corresponding source observation is unavailable. Average losses within each source family before combining them; inspect baseline-relative skill and gradient scales before fixing weights.
- Read contiguous stream chunks and compute shifted next-bar loss at eligible positions in one forward pass. Persist bars once and reference them by indices; never materialize every origin's repeated context. Freeze context semantics: a 128-token invocation emits shorter-prefix states at its beginning. Do not claim every emitted state had exactly 128 bars; define warm-up masking and streaming cache parity before use.
- Split by session date; fit transforms on the training split only. For 2026-top5-v1, reserve the existing validation interval and use an internal chronological training development split for repeated design choices. Purge targets across split boundaries. Balance ticker/session/timeframe exposure so very active streams do not dominate.
- Evaluate against persistence and simple activity baselines, separately by source, timeframe, session, and liquidity. Check future perturbation causality, missing data handling, chunk/inference parity, a small learning smoke test, and frozen embedding probes against random embeddings. Low prediction loss alone is not representation or trading generalization evidence.

## Data dependency

The reviewed dataset is D:\TradingML\runtimes\market_shards\datasets\2026-top5-v1 on the workstation, accessible through its Workstation-D share. Its existing trade-window API is 60x1s, 30x10s, 30x1m, 16x1h, and 20 daily; quote windows are 60x1s. This is not the proposed 128-bar 1s/15s/1m contract.

Verify complete underlying bar coverage, per-side size semantics, condition counts, warm-up, masks, and certified provenance. Derive 15s and 1m views only from complete compatible base bars. Missing sources or coverage remain explicit dependencies; do not zero-fill unknown conditions or silently reduce the contract. Pretraining must not depend on selected 1b positions or filter to profitable episodes.

## Proposed 1c trajectory labels

Preserve 1a opportunity labels and spread-aware 1b position selection unchanged. Add a separate, versioned 1c sidecar derived from accepted 1b positions, with original IDs and hashes retained.

For each accepted reference position, emit flat WAIT before entry, flat ENTRY at its selected entry clock, held HOLD between selected entry and exit clocks, held EXIT at the selected exit clock, and flat WAIT afterwards. State is measured before the action. Validate non-overlapping positions per ticker, ordering, boundary precedence, and session termination. Position age is a causal input; remaining duration is a target relative to this chosen trajectory, with censoring explicitly masked.

These are hard labels for reproducing the selected reference schedule. They do not establish that other entry/exit times are economically invalid. Preserve alternative opportunity windows and economic rejection reasons in separate fields for later use. A quote/evidence failure remains unknown even when the reference schedule says WAIT; represent schedule validity separately from economic supervision validity. Do not reinterpret a teacher trajectory as actual filled account history.

The reviewed v2 selection code distinguishes entry_quote_unavailable, target_quote_unavailable, target_not_1a_exit, and below_spread_score_threshold. Quote validity requires both sides, positive finite prices, valid sizes/counts, timestamps inside the exact completed second, and an uncrossed book. Rejected episodes receive WAIT before the per-row missing-quote check. Consequently saved WAIT alone is insufficient for economic negative mining.

## Deferred contrastive design

All downstream examples retain complete simultaneous market context. At each decision clock, use point-in-time scanner membership and retained holdings, never the future-known session ticker union as current membership. Supply causal position state and age. Store compact column/pair indices and provenance instead of duplicated contexts.

### Intra comparisons

Compare contextualized ticker embeddings within a market column, conditioned on pre-action state and lesson. For flat entry decisions, pair two qualified opportunities positively and a qualified opportunity with an economically rejected original entry negatively. Missing entry/target quotes and invalid target evidence are excluded from economic negatives. For held decisions, pair matching valid HOLD or EXIT targets positively and conflicting HOLD/EXIT targets negatively, using a consistent entry history. Under 1c, distinguish reference-schedule disagreement from economic disagreement; the former is not proof of the latter.

Use small task-specific projections so agreement on action does not imply agreement on remaining duration. Normalize embeddings and use bounded separation margins. Balance action/episode contributions and cap repeated near-identical rows. A column with one positive opportunity may still supply negative pairs; do not fabricate a positive counterpart.

### Inter comparisons

Initially compare whole columns within the same training session, with the same ticker identities, membership, and pre-action position-state pattern. Anchor at decision-signature changes. A positive has matching valid per-ticker decisions; a negative has at least one confirmed conflicting decision. Align by ticker identity, not by arbitrary label-based rematching. Equal action counts assigned to different tickers must remain distinguishable.

Use a balanced aggregate distance over corresponding ticker vectors so unchanged WAIT rows cannot dominate. Record unmatched anchors when no valid counterpart exists. Prefer varied contexts and cap adjacent duplicates. Mine difficult pairs only inside label-valid sets. Retain direct action supervision alongside contrastive loss.

Remaining-duration pairs use a separate projection, comparable position ages, similar durations for positives, and materially different durations for negatives; duration cutoffs must be fixed on training development data. Exclude censored or undefined duration targets. Preserve full recurrent histories or defined burn-in, rather than independently resetting every sampled column.

## Implementation order

1. Agree exact pretraining input, scaling, clock, and coverage contracts.
2. Implement and validate standalone causal pretraining and embedding export.
3. Implement 1c from selected 1b trajectories with validity and provenance preserved.
4. Audit pair availability and balance before implementing contrastive training.
5. Compare frozen pretrained embeddings with random-encoder controls; trading evaluation remains separate from pretraining metrics.

Source references: research/market_shards/v1/README.md, research/market_labels/v2/selection.py, and research/bar_gpt/v3/README.md. The spread-label manifest inspected on 2026-11-06 declares verified_review_only and review_only_not_optimizer_approved; this proposal does not promote that status.
