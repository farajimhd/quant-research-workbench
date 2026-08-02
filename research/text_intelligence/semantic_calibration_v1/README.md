# News Semantic Calibration V1

## Rule-only deterministic News V6

`deterministic_v6.py` is the production-compatible, rule-only successor to the
V5 News authority. It does not use TF-IDF, a statistical classifier, human
labels at inference, price reactions, or a serialized model artifact.

The durable configuration is readable Python data in
`deterministic_v6_config.py`: ordered article-structure patterns, source
metadata rules, meaningful issuer-evidence gates, explicit positive and
negative evidence weights, fixed direction thresholds, and explicit
eligibility rules. Analyst opinions, previews, editorials, roundups, mover
recaps, follow-ups, and automated summaries remain issuer-history context but
are not forecast triggers.

The 100 articles in `oss_gold_100_v3/shared/gold_sample.jsonl` are a frozen
acceptance set. Development excludes their exact sample IDs and requires the
other 900 articles:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v6 --phase development
```

Once the rules are locked, acceptance may run once. The launcher refuses to
overwrite an existing frozen result; another attempt requires a new authority
version:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v6 --phase frozen-acceptance
```

Generated predictions and metrics are written under the executing machine's
`TradingML/runtimes` root. The older `news_v6.py` remains only as the
historical learned TF-IDF/logistic research candidate. It is not this
deterministic V6 authority.

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

## Exhaustive coverage correction (V3)

V3 is the certified successor to the partial V2 review. It preserves every V2
judgment unless an explicit, rationale-bearing correction replaces or removes
that exact source unit. Corrections are bound to the immutable V2 unit hash, so
a stale correction cannot be applied after source drift. Every candidate ticker
must receive a reviewed disposition, and every retained issuer unit must have a
matching `labeled_issuer_unit` disposition.

The workflow is intentionally resumable and keeps all generated review queues,
decisions, annotations, audits, and evaluation reports below the configured
`TradingML/runtimes` collection root:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_coverage_review_v3
python -m research.text_intelligence.semantic_calibration_v1.run_inspect_coverage_review_v3
python -m research.text_intelligence.semantic_calibration_v1.run_record_coverage_review_v3 --help
python -m research.text_intelligence.semantic_calibration_v1.run_finalize_coverage_review_v3
python -m research.text_intelligence.semantic_calibration_v1.run_evaluate_coverage_v3
```

`run_amend_coverage_review_v3` provides a source-hash-bound repair path for a
reviewed disposition or issuer-unit correction. Its evidence-repair option may
restore only exact evidence already present in the immutable review queue; it
does not manufacture or infer semantic evidence.

The completed laptop authority contains 1,000 exhaustive V3 annotations, 7,826
explicit ticker dispositions, 734 analyst opinions across 254 articles, zero
audit errors, and result hash
`05b8eb30b99b65d962a762633daf77f3599022107882618bd42c21653a07fa42`.
The immutable V6 and V7 prediction products were re-evaluated against V3 truth
without overwriting their original V2 reports. The corrected evaluation is at
`coverage_review_v3/deterministic_evaluation.json` under the runtime collection.

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

The current exact benchmark is V6 with prompt V3 and typed instrument-candidate
contract V3. Every allowed candidate carries `canonical_instrument_id`,
`display_symbol`, and `instrument_type`; the model must return the exact
canonical ID. This removes the former contradiction between a bare-ticker
instruction and authoritative identifiers such as `X:UNIUSD`. Candidate V3
also recognizes explicit announced U.S. listing symbols such as "will trade on
the New York Stock Exchange under the ticker PRI" without treating foreign
exchange identifiers or arbitrary capitalized tokens as U.S. candidates.

The job is resumable: rerunning the same command reconciles existing remote
Batch jobs and never resubmits a completed model. Requests, raw responses,
validated predictions, failures, exact token usage, actual Batch cost, metrics,
and `COMPARISON.md` are written under
`D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\openai_gold_100_v6`.
All 100 articles remain in the scoring denominator; malformed or contract-invalid
responses are scored as missing predictions. Batch elapsed time includes queue
time and must not be interpreted as synchronous production latency.

The prior prompt-V1 outputs can be revalidated non-destructively under the
candidate repair:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_revalidate_gold_benchmark
```

That report is explicitly a historical prompt-V1/candidate-V2 audit. It is not
an exact prompt-V3 comparison. Since both the system prompt and structured
response field changed, every exact V6 request must be rerun; no paid request
is submitted without explicit cost authorization.

## Compare local instruction models through vLLM

The local V3 comparison reuses the exact OpenAI V6 population, prompt, output
schema, validator, dynamic multi-issuer output allowance, and all-100 scoring
denominator. Prepare its immutable runtime bundle on the laptop once:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_oss_gold_benchmark
```

The prepared `shared` directory must be synchronized to the workstation under
`D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\oss_gold_100_v3`.
It contains the frozen 100-article prompt-V3 package. The exact OpenAI V6
comparison is attached only after that paid benchmark completes; until then,
the local report states that the matching remote baseline is pending.

Serve one model at a time through vLLM with the exact public name and at least
a 65,536-token context. The long-context requirement is real: one untruncated
gold article is approximately 59,200 `o200k_harmony` input tokens. The generic
launcher applies the required model-family flags. A model already downloaded
under `/mnt/d/models_artifacts/opensource/huggingface` is staged once into the
WSL-native cache at `~/.cache/quant-research-workbench/vllm-models`; vLLM loads
only from that native cache. Staging is resumable through `rsync`, verifies the
Hugging Face revision plus blob count and bytes, rejects 9P/DrvFS destinations,
and is skipped on later starts when the completion marker still matches. If a
model has no durable mounted copy yet, Hugging Face downloads it directly into
the native cache. Ensure WSL has enough disk space for the selected model; the
Qwen checkpoint needs approximately 67 GiB plus working headroom. Install
`rsync` in WSL if it is absent.
Before invoking `vllm`, the launcher starts Bash and sources
`~/.venvs/vllm/bin/activate`; if that environment is missing, serving stops
immediately instead of falling back to an unrelated system installation. The
Python entry point invokes the adjacent Bash launcher as a file and passes each
value as a separate process argument; it does not embed a multiline `bash -lc`
command that Windows or `wsl.exe` can reinterpret.
The launcher also enables WSL2 pinned memory explicitly. vLLM disables it by
default under WSL, but the V2 model runner selected for dense models such as
Mistral requires pinned host memory for CUDA Unified Virtual Addressing. Leave
this default enabled. `--disable-wsl-pin-memory` exists only for diagnosing an
older WSL/kernel installation and may require separately forcing a compatible
legacy model runner.
For the existing GPT-OSS comparison:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_vllm_benchmark_server_wsl `
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
python -m research.text_intelligence.semantic_calibration_v1.run_vllm_benchmark_server_wsl `
  --profile 120b --max-model-len 65536

python -m research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark `
  --profile 120b --execute
```

The two additional, deliberately different instruction-following candidates
are:

- `qwen35-a3b`: `Qwen/Qwen3.5-35B-A3B`, a 35B-total/3B-active MoE. The server
  uses language-model-only mode and the benchmark disables thinking so the
  response is the requested JSON rather than a hidden reasoning stream. Its
  hybrid Mamba cache is explicitly bounded to 64 concurrent sequences; this is
  well above the four benchmark workers and avoids vLLM's incompatible 1,024
  sequence CUDA-graph default at the required 65,536-token context.
- `mistral-small-3.1-24b`:
  `mistralai/Mistral-Small-3.1-24B-Instruct-2503`, selected for its explicit
  structured-JSON and instruction-following capability and independent model
  family. Its official Mistral tokenizer/config/load modes are applied.

Run Qwen in terminal 1; the first start downloads it automatically:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant-research-workbench
python -m research.text_intelligence.semantic_calibration_v1.run_vllm_benchmark_server_wsl `
  --profile qwen35-a3b --max-model-len 65536
```

After the server reports startup complete, use terminal 2:

```powershell
conda activate ml4t
cd D:\TradingML\codes\quant-research-workbench
python -m research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark `
  --profile qwen35-a3b --execute
```

Stop the Qwen server after completion, then repeat for Mistral:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_vllm_benchmark_server_wsl `
  --profile mistral-small-3.1-24b --max-model-len 65536

python -m research.text_intelligence.semantic_calibration_v1.run_oss_gold_benchmark `
  --profile mistral-small-3.1-24b --execute
```

Do not run the two servers concurrently on one 96 GB GPU. Do not pre-download
the full Mistral repository with a blind snapshot command: that repository can
contain alternate weight layouts. Letting vLLM resolve the files required by
its Mistral loader avoids redundant downloads. If Qwen reports an unsupported
architecture, the installed vLLM build is too old for Qwen3.5; upgrade that WSL
environment before changing model or quantization settings.

The inference launcher is resumable per article and refuses a mismatched model,
sample, rendered-text hash, annotation hash, or response contract. Each run
writes responses, predictions, failures, usage, local wall time, request time,
and completion-token throughput beneath its workstation runtime root. Once
any local run completes, `COMPARISON_WITH_OPENAI.md` is refreshed. OpenAI Batch
time and local vLLM wall time remain separately identified because they are not
latency-equivalent. Truncated generations retry with a larger bounded output
budget, while invalid structured responses retry with a concise contract repair
instruction; successful articles are never repeated.
Local API cost is zero, but the report does not misrepresent unmetered GPU,
electricity, depreciation, or operator time as zero compute cost.
## Balanced Sol teacher corpus

The human-reviewed `news_1000` collection remains the calibration and frozen
acceptance authority. It must never be used as paid teacher-training input.
The independent `news_sol_teacher_corpus_v1` product selects 10,000 other
canonical News articles with these contracts:

- exact zero overlap with every source identity in the completed 1,000-item
  ground-truth manifest;
- equal quotas across each calendar year from 2010 through 2026;
- a deliberate provider-ticker scope mix of 15 percent zero, 50 percent single,
  and 35 percent multi-ticker articles within every year, followed by
  deterministic round-robin coverage across V5 label presence, content role,
  source origin, direction, eligibility, text length, and rare event concepts;
- a 15 percent per-scope target for V5-missing articles when that population is
  available, with its realized count recorded rather than fabricated;
- source-independent candidate supplementation so V5-missing articles can be
  selected rather than allowing the existing classifier to define its own
  teacher population;
- canonical rendered V2 text and point-in-time issuer candidates as the only
  Sol input; V5 selection hints are sealed and never placed in the prompt; and
- immutable item, selection, exclusion, and manifest hashes under the machine
  runtime root.

Prepare the corpus on the machine whose runtime already contains the certified
`news_1000/sample_manifest.json` authority. Runtime products are machine-local;
do not copy the laptop corpus into a workstation code directory. The currently
certified 10,000-item build is on the laptop, so this preparation command is
shown for the laptop source repository:

```powershell
conda activate ml4t
cd D:\TradingCodes\quant-research-workbench
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_sol_teacher_corpus
```

Plan the GPT-5.6 Sol Batch job without making a paid request:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_sol_teacher_labels
```

Review the exact input and expected-cost plan printed by that command. To
authorize the bounded run:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_sol_teacher_labels --execute --authorize-cost-usd 225
```

The launcher uses at most 250 requests per Batch partition and also closes a
partition before its conservative input estimate would exceed 1.2 million
tokens. Only partitions whose combined active input remains within that same
1.2-million-token admission ceiling can be enqueued. This keeps the job below
the authenticated organization's current 1.35-million enqueued-token limit;
`--max-enqueued-input-tokens` makes the local ceiling explicit if that external
limit changes. Before submitting a partition it reserves the maximum permitted
cost of all active work plus that partition. Completed usage releases its reserve and is charged using separate
uncached-input, cached-input, cache-write, and output counters. Therefore, the
job can progress near the measured expected cost without permitting total
authorized spend above the explicit command authorization. The immutable
launcher ceiling is $250; the current 10,000-item corpus plans at a conservative
$195.64 expected cost, so the documented $225 authorization leaves bounded
variance without authorizing the theoretical all-output-token maximum.
Rerunning the same command reconciles remote Batches and resumes from durable
runtime state. A whole-Batch rejection that processed zero requests is not
misreported as article-level failure: retryable capacity and service errors are
retained as attempt history and retried with bounded exponential backoff.
Request-level output failures remain durable final evidence. The V2 execution
plan can reuse already completed V1 labels and their billed usage while leaving
the original states and failure artifacts unchanged. `--no-wait` submits only
the partitions that fit both rolling authorization ceilings and then returns.

The Batch API currently permits up to 50,000 requests and a 200 MB JSONL input
file. This pipeline intentionally uses much smaller rolling partitions to make
cost authorization, recovery, and output auditing bounded.

## Deterministic News V7 candidate

V7 is the rule-only successor to V6. It was developed against the 900-item
development split and evaluated once against the sealed 100-item acceptance
split after its rules and tests were locked. It does not use price reaction,
Sol output, a learned classifier, sample identities, or headline-specific
exceptions.

The repair adds ordered structural role detection, provider-linked issuer scope,
separate trigger and history eligibility, and generalized handling of mover
recaps, market roundups, why-moving follow-ups, analyst previews, regulatory
events, and direct issuer events. Large roundup annotations were not treated as
exhaustive when their rendered text contained legitimate additional issuer
events.

Run development evaluation:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v7 --phase development
```

Run the sealed acceptance split only for a newly locked authority version:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v7 --phase frozen-acceptance
```

Generate the final comparison under the machine runtime root:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.deterministic_v7_audit
```

The first sealed result improved extraction F1 from 0.901 to 0.911, ticker-scope
F1 from 0.693 to 0.697, role macro F1 from 0.531 to 0.573, origin macro F1 from
0.441 to 0.450, and concept-family F1 from 0.369 to 0.370. Direction macro F1
was unchanged at 0.415, history eligibility was unchanged at 0.876, and forecast
eligibility declined from 0.426 to 0.415. V7 is therefore a better structural
candidate, not a certified global replacement for V6's trigger authority.

## Deterministic News V8 candidate and Sol teacher audit

V8 uses the disjoint Sol teacher corpus only to discover recurring structural
and phrase-level gaps. The teacher run produced 9,997 validated labels and
three durable request failures from the immutable 10,000-article selection.
Sol output is not acceptance truth: candidate rules were selected using the
900 human-reviewed development articles, and the sealed 100 human-reviewed
articles were evaluated exactly once after V8 was locked. The mixed frozen
result below is why this remains a candidate rather than a production cutover.

The rule-only repair adds:

- high-precision provider-tag and channel evidence for mover, market-update,
  preview, automated, and analyst publication types;
- issuer-resolved context retention only when the passage contains a semantic
  event, excluding price-only and schedule-only symbol lists;
- explicit financial-language rules for direct beats and misses, guidance,
  regulatory progress and setbacks, clinical results, demand, dilution,
  filing delays, material weaknesses, and transaction accretion/dilution;
- trigger eligibility derived from the scoped event and publication role,
  without inheriting V5's stale document-level eligibility gate; and
- a bounded 16-process teacher comparison runner whose output remains under the
  machine runtime root.

Run human development evaluation:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v8 --phase development
```

Run the Sol error-discovery comparison:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_compare_sol_teacher --authority v8 --workers 16
```

The resulting comparisons are:

| Metric | Human dev V7 | Human dev V8 | Frozen V7 | Frozen V8 | Sol V7 | Sol V8 |
|---|---:|---:|---:|---:|---:|---:|
| Extraction F1 | 0.915 | 0.921 | 0.911 | 0.918 | — | — |
| Ticker-scope F1 | 0.552 | 0.558 | 0.697 | 0.703 | 0.807 | 0.811 |
| Content-role macro F1 | 0.715 | 0.730 | 0.573 | 0.582 | 0.486 | 0.504 |
| Source-origin macro F1 | 0.605 | 0.604 | 0.450 | 0.425 | 0.478 | 0.487 |
| Direction macro F1 | 0.403 | 0.428 | 0.415 | 0.414 | 0.398 | 0.421 |
| Concept-family F1 | 0.428 | 0.453 | 0.370 | 0.390 | 0.259 | 0.300 |
| Forecast eligibility F1 | 0.632 | 0.685 | 0.415 | 0.429 | 0.682 | 0.756 |
| Issuer-history eligibility F1 | 0.888 | 0.899 | 0.876 | 0.882 | 0.949 | 0.949 |

The frozen result confirms better extraction, ticker scope, role, concepts,
forecast eligibility, and history eligibility. It does not confirm a direction
improvement, and source-origin macro F1 regressed. V8 is therefore a materially
better candidate for structural classification and eligibility, but it is not
a certified wholesale production replacement for the existing sentiment and
source-origin authorities. The frozen weaknesses must inform a separately
versioned future authority; they must not be tuned back into V8.

## Deterministic News V9 candidate

V9 uses the completed 9,997-label Sol corpus as broad weak supervision while
keeping inference entirely deterministic. It does not call Sol, load a learned
model, read teacher labels, or depend on calibration artifacts at runtime. Its
runtime authority is a source-controlled set of readable structural rules,
concept additions, eligibility decisions, signed concept weights, and direction
thresholds applied on top of V8.

The teacher corpus is partitioned before calibration into 7,997 development,
1,000 validation, and 1,000 locked-test articles. Articles sharing a provider
and normalized headline template remain in the same partition, which prevents
template variants such as recurring analyst or mover headlines from leaking
across the boundaries. The split contains 9,666 groups, its largest group has
67 articles, and its verified cross-partition group leakage is zero. Sol remains
weak supervision rather than acceptance truth.

The calibration process is deliberately constrained:

- categorical overrides require minimum support, precision, and improvement;
- concept additions apply only to single-ticker articles;
- signed direction weights cannot reverse a concept's defined polarity;
- thresholds are selected on validation after weights are fitted on development;
- teacher-supported rules that regress the 900 reviewed human development
  articles are rejected and recorded in the runtime config; and
- sample IDs, source IDs, and headline-specific exceptions are never runtime
  features.

Prepare the immutable grouped split and fit a fresh calibration report under
the configured runtime root:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_deterministic_v9_split
python -m research.text_intelligence.semantic_calibration_v1.run_fit_deterministic_news_v9 --workers 16
```

Evaluate the frozen runtime configuration against the human collection and the
teacher partitions:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_deterministic_news_v9
python -m research.text_intelligence.semantic_calibration_v1.run_compare_sol_teacher --authority v9 --split locked_test --workers 16
```

Locked Sol test results show improvements on every changed dimension:

| Metric | V8 | V9 |
|---|---:|---:|
| Content-role macro F1 | 0.500 | 0.509 |
| Source-origin macro F1 | 0.497 | 0.501 |
| Direction macro F1 | 0.396 | 0.401 |
| Concept-family F1 | 0.297 | 0.323 |
| Forecast eligibility F1 | 0.803 | 0.814 |
| Ticker-scope F1 | 0.831 | 0.831 |
| Issuer-history eligibility F1 | 0.931 | 0.931 |

The independently reviewed evidence is a guard, not an untouched acceptance
set, because all 1,000 articles were used during earlier authority development.
On the 900-item human development subset, V9 improves role from 0.730 to 0.735,
origin from 0.604 to 0.613, concepts from 0.453 to 0.467, and forecast
eligibility from 0.685 to 0.721; direction changes from 0.428 to 0.427. On the
historical 100-item frozen subset, V9 improves role from 0.582 to 0.623, origin
from 0.425 to 0.428, direction from 0.414 to 0.434, concepts from 0.390 to
0.396, and forecast eligibility from 0.429 to 0.456.

Direction remains the limiting dimension. On the locked teacher test, V9's
neutral-class F1 is 0.218 and the overall direction gain comes primarily from
better mixed-event recognition. The candidate must therefore remain outside
production until the intended authority cutover is separately reviewed and
approved.

## TF-IDF bagged random-forest News V10 experiment

V10 tests whether a learned bagging baseline can capture nonlinear semantic
combinations that the readable V9 rules miss. It trains only from the 9,997
valid Sol teacher articles and evaluates on all 1,000 independently reviewed
human articles. Human labels are never used to fit the text representation,
forest heads, or thresholds. The Sol and human article sets are disjoint.

The representation uses word and character TF-IDF features. Truncated SVD,
fitted only on the Sol training corpus, produces bounded dense article and
issuer-context vectors suitable for trees. Issuer names, aliases, and ticker
symbols are replaced with a target-entity marker in issuer-scoped examples so
the forest cannot solve the task by memorizing issuer identity. Separate
bootstrap random-forest heads predict extraction, issuer scope, role, origin,
direction, concepts, and eligibility. Forest depth is bounded through leaf
limits, and each tree samples 80 percent of its training rows.

Run the complete train/evaluate comparison with:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_news_v10
```

The generated model and evaluation report are written outside the repository
under `D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_v10_tfidf_random_forest`.

Results on all 1,000 human-reviewed articles are:

| Metric | V9 deterministic | V10 TF-IDF forest | V10 - V9 |
|---|---:|---:|---:|
| Extraction F1 | 0.921 | 0.934 | +0.013 |
| Ticker-scope F1 | 0.575 | 0.522 | -0.053 |
| Content-role macro F1 | 0.726 | 0.591 | -0.135 |
| Source-origin macro F1 | 0.594 | 0.562 | -0.032 |
| Direction macro F1 | 0.431 | 0.414 | -0.017 |
| Concept-family F1 | 0.458 | 0.244 | -0.214 |
| Forecast eligibility F1 | 0.697 | 0.828 | +0.131 |
| Issuer-history eligibility F1 | 0.897 | 0.959 | +0.063 |

V10 is not a replacement for V9. Its high-recall issuer and concept heads
over-predict substantially: issuer scope has 4,514 false positives and concept
extraction has 9,678 false positives on the human set. It also nearly loses the
regulatory-event role class (F1 0.053 versus V9's 0.613). The useful finding is
more specific: learned TF-IDF features materially improve eligibility recall
and article extraction. A future hybrid may use those heads as advisory signals
behind deterministic structural constraints, but no production cutover is
authorized by this experiment.

### Fresh prediction-blind 100-article acceptance set

The original 1,000 human articles were used while V9 was developed and are
therefore no longer an untouched acceptance set. A separate 100-article set was
selected and manually annotated before either V9 or V10 predictions were
generated. The selection excludes every source ID in both the 1,000-article
human authority and the 10,000-article Sol teacher corpus.

The frozen set has:

- 100 articles and zero training/development overlap;
- coverage from 2010 through 2026, with six articles per year through 2024 and
  five per year in 2025 and 2026;
- 49 single-ticker, 34 multi-ticker, and 17 zero-ticker articles; and
- balanced selection across the available V5 structural strata, while never
  exposing article-level V9 or V10 output to the reviewer.

The completed annotations extend the runtime-only human authority from 1,000
to 1,100 articles. Prepare, record, certify, and evaluate the set with:

```powershell
python -m research.text_intelligence.semantic_calibration_v1.run_prepare_fresh_acceptance
python -m research.text_intelligence.semantic_calibration_v1.run_record_fresh_acceptance --input-jsonl <manual-review.jsonl>
python -m research.text_intelligence.semantic_calibration_v1.run_finalize_fresh_acceptance
python -m research.text_intelligence.semantic_calibration_v1.run_evaluate_fresh_acceptance
```

Fresh-set results are:

| Metric | V9 deterministic | V10 TF-IDF forest | V10 - V9 |
|---|---:|---:|---:|
| Extraction F1 | 0.880 | 0.931 | +0.052 |
| Ticker-scope F1 | 0.456 | 0.395 | -0.061 |
| Content-role macro F1 | 0.447 | 0.430 | -0.017 |
| Source-origin macro F1 | 0.388 | 0.494 | +0.106 |
| Direction macro F1 | 0.378 | 0.424 | +0.046 |
| Concept-family F1 | 0.486 | 0.222 | -0.265 |
| Forecast eligibility F1 | 0.722 | 0.849 | +0.127 |
| Issuer-history eligibility F1 | 0.882 | 0.952 | +0.070 |

The untouched set confirms that V10's learned text representation improves
article extraction and eligibility decisions, and modestly improves direction.
It also confirms a material structural weakness: V10 over-predicts tickers and
concepts, with concept precision only 0.132 despite recall of 0.691. V9 remains
the stronger concept authority and is still better for ticker scope and content
role. Consequently, neither result supports replacing V9 wholesale with V10;
V10 is suitable only as an advisory source for the dimensions on which it has
demonstrated fresh-set gains.
