# News Semantic Calibration V1

This package creates and validates a persistent human-reviewed semantic ground
truth collection for the deterministic News authority. It does not use market
reaction as a sentiment label and does not automate semantic annotation.

Generated samples, annotations, review state, fitted weights, plots, and
reports belong under the executing machine's `TradingML/runtimes` root. They
must never be written into this repository.

## Blinding contract

The first review pass exposes the original rendered publication, source
metadata, provider ticker links, and point-in-time issuer candidates. It hides
all V5 concepts, directions, scores, eligibility, later price action, and the
locked train/calibration/test assignment. Hidden comparison data is stored in a
separate sealed runtime file and is not read during annotation.

Every annotation is issuer-scoped and hash-bound to the immutable source item.
It records document decisions, evidence quotes, roles, concepts, modality,
exact source-field evidence spans, time orientation, independent positive and
negative evidence levels, a written semantic rationale, semantic
direction, three eligibility decisions, reviewer confidence, ambiguity, and
taxonomy proposals. Review output is retained for future taxonomy revisions
and V6 evaluation.

V2 adds a dedicated textual analyst-opinion contract. Ratings and targets are
stored in separate from/to fields. No market reaction, target attainment, or
analyst correctness is joined during labeling. The immutable V1 pilot remains
under `annotations`; the explicit second review is stored under
`annotations_v2`, with drafts in `annotation_templates_v2`.

Reviewers select verbatim evidence quotes. The persistence authority resolves
each quote to a unique exact field/character span and rejects absent or
ambiguous evidence; it does not infer or choose semantic evidence.

Large manual records may be passed through `run_record_annotation` as bounded
base64 chunks with `--stage-sample`, `--stage-index`, `--stage-total`, and
`--stage-base64`, then committed with `--finalize-staged SAMPLE_ID`. Chunk
staging lives under the runtime root, is hash-checked and resumable, and is
deleted only after the assembled annotation passes the normal schema, source,
evidence-span, and immutable persistence checks. This changes transport only;
it does not infer or automate semantic judgments.

## Evidence levels

- `0`: none
- `1`: weak
- `2`: moderate
- `3`: strong
- `4`: exceptional

These ordinal human labels are targets for globally fitted constrained concept
weights. Reviewers must not invent per-article rule weights.

## Review sequence

1. Build one immutable 1,000-publication stratified manifest.
2. Review a blinded 100-publication taxonomy pilot.
3. Freeze the guidelines and re-review the pilot.
4. Complete the remaining first-pass annotations.
5. Re-review all disagreements and a random agreement sample without price
   reaction.
6. Lock ground truth before fitting weights or selecting thresholds.
7. Evaluate once on the sealed holdout.

## Prepare the immutable sample

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_sample
```

The default output is
`D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000`
on the laptop. Re-running the command reuses the immutable manifest; it never
silently replaces a collection. `blinded_articles` and `annotation_templates`
are reviewer-visible. `sealed/v5_comparison_and_splits.json` must remain closed
until the annotation and adjudication passes are locked.

Validate hashes, blinding, uniqueness, rendered-text presence, and corpus
coverage without exposing sealed comparison values:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_audit_sample
```

Prepare the existing immutable pilot for the V2 review round:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_review_round
```

This migration does not infer analyst fields. It marks analyst-related units
for manual re-review and carries all other semantic judgments forward without
consulting V5 or market reaction.

After inspecting the review manifest, mechanically persist only records with
no analyst evidence:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_review_round --carry-non-analyst
```

Analyst-related drafts are never carried by this option; they remain blocked on
manual source-text review and structured opinion extraction.

After the V2 pilot audit passes, prepare the remaining blinded first-pass
templates without exposing sealed data:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_review_round --prepare-remaining
```

Audit every persisted V2 record, its immutable hash, schema, pilot coverage,
and exact unit/opinion evidence spans:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_audit_annotations
```

Build the text-only analyst/entity glossary from completed V2 reviews:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_build_analyst_glossary
```

The glossary records article-observed names, aliases, firms and attributions.
It does not join reaction data and does not treat first/last observation as a
certified employment interval.

## Compare the current News V5 authority

After all V2 annotations pass their audit and the collection is locked, rerun
the actual current News V5 authority against the immutable rendered products:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_compare_v5
```

This writes resumable predictions and split-specific field-level reports below
the runtime collection. It does not query price reactions, modify annotations,
or evaluate SEC documents.

## Fit and evaluate the News V6 research candidate

After the V5 comparison is present, run:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_fit_news_v6
```

The candidate uses sparse deterministic text features and linear classifiers.
It fits on the sealed fit split, selects thresholds only on calibration, and
writes the locked-holdout comparison and serialized research artifact beneath
the runtime collection. It is not a production authority and must not be used
for live classification or a historical backfill until its scope recall and
external validation are certified.

## Benchmark OpenAI teacher models on 100 gold articles

Plan the deterministic stratified 100-article comparison without making a
paid request:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_openai_gold_benchmark
```

The plan covers GPT-5.6 Sol, Terra and Luna, GPT-5.4 mini and nano, and GPT-4.1
mini and nano. It uses the same strict issuer-scoped output contract for every
model, sizes the output allowance for broad multi-issuer articles, and enforces
both an explicit authorization and a hard $20 ceiling. Submit and wait only
after inspecting the protected total printed by the plan:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_openai_gold_benchmark `
  --execute --authorize-cost-usd <PROTECTED_TOTAL>
```

The current exact benchmark is V5 with prompt V2 and issuer-candidate contract
V2. Prompt V2 tells the model that output tickers must come from provider
tickers or point-in-time candidates, and candidate V2 recognizes explicit
announced U.S. listing symbols such as "will trade on the New York Stock
Exchange under the ticker PRI" without treating foreign exchange identifiers
or arbitrary capitalized tokens as U.S. candidates.

The job is resumable: rerunning the same command reconciles existing remote
Batch jobs and never resubmits a completed model. Requests, raw responses,
validated predictions, failures, exact token usage, actual Batch cost, metrics,
and `COMPARISON.md` are written under
`D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\openai_gold_100_v5`.
All 100 articles remain in the scoring denominator; malformed or contract-invalid
responses are scored as missing predictions. Batch elapsed time includes queue
time and must not be interpreted as synchronous production latency.

The prior prompt-V1 outputs can be revalidated non-destructively under the
candidate repair:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_revalidate_gold_benchmark
```

That report is explicitly post-hoc. It shows which candidate inputs changed
and which stored outputs remain valid, but it is not mislabeled as an exact
prompt-V2 comparison. Since the shared system prompt changed, every exact V5
request must be rerun; no paid request is submitted without explicit cost
authorization.

## Add workstation GPT-OSS 20B and 120B

The local V2 comparison reuses the exact OpenAI V5 population, prompt, output
schema, validator, dynamic multi-issuer output allowance, and all-100 scoring
denominator. Prepare its immutable runtime bundle on the laptop once:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_oss_gold_benchmark
```

The prepared `shared` directory must be synchronized to the workstation under
`D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\oss_gold_100_v2`.
It contains the frozen 100-article prompt-V2 package. The exact OpenAI V5
comparison is attached only after that paid benchmark completes; until then,
the local report states that the matching remote baseline is pending.

Serve one model at a time through vLLM with the exact public name and at least
a 65,536-token context. The long-context requirement is real: one untruncated
gold article is approximately 59,200 `o200k_harmony` input tokens. For example:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_server_wsl `
  --profile 20b --max-model-len 65536
```

Wait for `/v1/models` to expose `openai/gpt-oss-20b`, then run:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark `
  --profile 20b --execute
```

Stop the 20B server, start 120B with the same context and served-name contract,
then run:

```powershell
python -m research.news_labeling.gpt_oss_v1.run_server_wsl `
  --profile 120b --max-model-len 65536

python -m research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark `
  --profile 120b --execute
```

The inference launcher is resumable per article and refuses a mismatched model,
sample, rendered-text hash, annotation hash, or response contract. Each run
writes responses, predictions, failures, usage, local wall time, request time,
and completion-token throughput beneath its workstation runtime root. Once
either OSS run completes, `COMPARISON_WITH_OPENAI.md` is refreshed; after both
complete it contains all nine models. OpenAI Batch time and local vLLM wall
time remain separately identified because they are not latency-equivalent.
Local API cost is zero, but the report does not misrepresent unmetered GPU,
electricity, depreciation, or operator time as zero compute cost.
