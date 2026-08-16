# Forecast Eligibility Mismatch Blind Audit Handoff

Use the prompt below in a new Codex task to continue the forecast-label audit
without loading this chat. The runtime dataset is immutable; generated packets,
worker outputs, ledgers, and reports must remain under `D:\TradingML\runtimes`.

## Prompt for the next task

```text
Continue the correction-grade blind audit of the consolidated forecast-eligibility
authority in D:\TradingCodes\quant-research-workbench.

Read first:

1. AGENTS.md
2. TASK_HISTORY.md Current Focus and TASK-0191 only
3. docs/codex/FORECAST_ELIGIBILITY_MISMATCH_BLIND_AUDIT_PROMPT.md
4. docs/codex/chat-summaries/2026/CHAT-20260815-UNKNOWN-forecast-labeling-metadata-audits.md
5. D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_v1\LOAD_MANIFEST.json
6. The REPORT.json, VALIDATION.json, and HASH_MANIFEST.json in that same runtime directory

Objective:

Blindly review all 35,995 combined-model/current-label mismatches and create a
new versioned successor authority with only correction-grade adjudicated label
changes. Do not mutate authority_v1 and do not automatically replace labels
with model predictions.

Current frozen authority:

D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_v1

Load these tables:

- Primary article labels:
  article_forecast_eligibility_labels.jsonl
  Key: source_id
  Rows: 361,695
  Labels: 139,068 eligible; 221,219 ineligible; 1,408 insufficient_short_text

- Gold-only issuer sentiment:
  gold_issuer_sentiment_labels.jsonl
  Key: unit_id
  Rows: 16,983 issuer units across 15,062 articles
  Do not create sentiment labels for 2025 or 2026 fast-label rows.

- Controller-only mismatch inventory:
  mismatch_audit_controller.jsonl
  Key: source_id
  Rows: 35,995
  This file contains current labels, model predictions, probabilities, tags,
  channels, and controller audit state. Never expose those fields to semantic
  reviewers.

- Prior blind audit evidence:
  blind_audit_observations.jsonl
  Rows: 1,000: 500 mismatch samples and 500 match samples.
  These were single-reader audit estimates and were deliberately not applied
  to the primary label table. Of the mismatch sample, 295/500 current labels
  were judged wrong. Of the match sample, 37/500 were judged wrong.

- Applied metadata-audit provenance:
  metadata_correction_ledger.jsonl (3,137 applied multi-reader corrections)
  unresolved_three_way_votes.jsonl (12 unresolved three-way votes)

Exact rendered-text authority, referenced by LOAD_MANIFEST.json:

D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_rf_comparison_v1\rendered_texts.jsonl

Join on source_id. Verify the complete file SHA-256 from LOAD_MANIFEST.json and
verify each rendered_text_hash before packetization. Never reconstruct source
text from evidence excerpts, labels, titles, or metadata.

Population facts:

- 360,287 decisive labels were scored by the combined metadata+TF-IDF model.
- 35,995 are model/current-label mismatches (9.99%).
- 33,071 are current ineligible -> model eligible.
- 2,924 are current eligible -> model ineligible.
- A prediction-blind random audit of 500 mismatches judged 295 current labels
  wrong: 59.0%, Wilson 95% CI 54.6%-63.2%.
- A prediction-blind random audit of 500 matches judged 37 current labels
  wrong: 7.4%, Wilson 95% CI 5.4%-10.0%.
- The model is a triage signal, not semantic authority.

Blindness contract:

The root controller may read the controller table. Semantic workers may receive
only opaque packet ID, source_id, published_at_utc, complete rendered text, and
rendered-text SHA-256. Do not show workers:

- current label
- model prediction, probability, margin, or match direction
- train/test split or source dataset
- tags, channels, provider classification, ticker count, or metadata patterns
- earlier manual labels, rationales, votes, audit state, or other worker output
- filenames or packet ordering that reveal a label or model direction

The semantic decision must be based only on the assigned article text. Date and
canonical ticker identity may be supplied only when required to identify the
tradable issuer; metadata tags/channels must remain hidden because they were
model features and would make the audit circular.

Eligibility policy:

Eligible only when the supplied text reports a new or current potentially
material event or issuer guidance for an identifiable tradable issuer.
Ineligible includes analyst-rating/price-target-only articles, earnings or
event previews, recaps and price-movement explanations based only on already
reported events, technical/valuation/short-interest commentary, routine halt,
resumption, listing or index notices, generic macro/political commentary,
screeners/lists, and background/reference articles. Use insufficient only when
the complete supplied text cannot establish the decision reliably.

Worker output per article:

- source_id
- manual_label: eligible | ineligible | insufficient_information
- confidence_probability
- controlled reason_code
- rationale of at most 30 words
- one exact verbatim evidence excerpt of at most 240 characters
- isolation attestation confirming only assigned article text was used

Mechanical packetization only:

- Freeze an exact 35,995-source-ID manifest before review.
- Use approximately 25,000 source tokens or 80,000 rendered characters per
  packet, normally no more than 45 articles.
- Put an individually oversized article in a solo packet; never truncate.
- Preserve source order and hashes.
- Use create-new writes and a restart-safe packet ledger.
- Validate exact coverage, membership, order, uniqueness, evidence containment,
  schema, attestation, and reconciliation after every tranche.

Agent plan:

- Use a fixed reusable pool of at most three semantic workers plus the root
  controller. Do not create one worker per packet and do not allow nested
  delegation.
- Reuse workers through bounded follow-up tranches and store bulk results only
  under D:\TradingML\runtimes.
- Process all 35,495 currently unreviewed mismatches once.
- The 500 rows marked single_blind_complete require an independent second blind
  read; do not treat the previous audit estimate as final correction authority.
- Independently second-read all 2,924 current-eligible/model-ineligible rows,
  every low-confidence or insufficient first-pass result, every invalid output,
  and a deterministic 10% sample of the remaining direction. Selection is
  controller-only and must not leak to workers.
- Send every disagreement to a fresh third blind adjudication. The third reader
  must not see either prior decision.
- Include the 12 unresolved_three_way_votes in a fresh correction-grade lane
  even if they are outside the mismatch inventory.

Reconciliation and successor authority:

- Never auto-flip a label because the model disagrees.
- Apply a correction only after the required independent blind review and valid
  evidence. Preserve every vote and provenance record.
- Keep the 1,408 insufficient_short_text rows explicit; they require a separate
  source-restoration/full-text program and must not be guessed.
- Write a new immutable successor, suggested name:
  D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_v2
- Copy gold issuer sentiment byte-for-byte unless a separate sentiment audit is
  explicitly authorized. Forecast review must not change sentiment.
- Preserve original label, corrected label, source hash, reviewer provenance,
  evidence, reason code, authority level, and the model version that triggered
  review.
- Validate 361,695 exact unique article IDs, source membership, label counts,
  audit coverage, correction ledger, hashes, and zero forecast/sentiment scope
  mixing. Produce REPORT.json, VALIDATION.json, HASH_MANIFEST.json, and a
  controller reconciliation table.

Evaluation safeguard:

Because the existing combined model selected the mismatches, do not use its
post-correction performance on the same selectively reviewed test rows as the
sole generalization claim. After correction, retrain only from the corrected
training authority and establish a fresh independently audited holdout, or
fully audit the existing test population before treating it as a final
benchmark.

Before starting the large campaign, report the exact packet count, token/byte
volume, fixed worker pool, expected runtime artifact volume, restart design,
and first bounded tranche. Do not exceed eight newly created subagents without
separate user approval; the intended design needs only three reusable workers.
```

## Loading example

The canonical entrypoint is the runtime `LOAD_MANIFEST.json`; do not hard-code
neighboring intermediate versions:

```python
import json
from pathlib import Path

manifest_path = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v1\LOAD_MANIFEST.json"
)
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

article_labels_path = Path(
    manifest["primary_tables"]["article_forecast_eligibility"]["path"]
)
mismatch_controller_path = Path(
    manifest["audit_tables"]["mismatch_controller"]["path"]
)
rendered_texts_path = Path(
    manifest["external_source_text_authority"]["path"]
)
```

The mismatch controller is deliberately not a worker packet. A controller must
join it to the rendered-text authority, strip every hidden field listed above,
randomize opaque packet order, and only then write semantic worker packets.
