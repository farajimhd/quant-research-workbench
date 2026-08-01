# News semantic labeling benchmark v1

Date: 2026-08-01

Scope: News text only; SEC semantic labeling is not evaluated here.
Ground truth: the persistent, blinded, human-reviewed News collection under the semantic-calibration runtime.

## Executive conclusion

No evaluated system is best at every part of the label contract.

- **GPT-5.6 Sol is the strongest broad semantic judge observed**: quality `0.694`, direction macro F1 `0.776`, and forecast/reaction eligibility F1 `0.795`. This result comes from the older prompt-V1 OpenAI Batch run after candidate-contract revalidation; it is not an exact prompt-V3 run.
- **Qwen3.5-35B-A3B is the strongest exact prompt-V3 local model**: quality `0.493`, direction macro F1 `0.544`, forecast eligibility F1 `0.600`, and 99 valid outputs. It is the best current local teacher candidate, not a certified production authority.
- **Mistral Small 3.1 24B does not justify its cost in this run**: its quality (`0.479`) is effectively tied with GPT-OSS 120B (`0.479`), but it produced only 90 valid outputs and ran at 3.15 articles/minute.
- **The production V5 deterministic rules are fast but not accurate enough as the sole semantic authority**: quality `0.391`, event-concept F1 `0.321`, direction macro F1 `0.400`, and forecast/reaction eligibility F1 `0.193`. They over-predict eligible issuer units and collapse many positive and negative units to neutral.
- **The V6 calibrated sparse classifier materially improves V5 on its sealed 218-article holdout**: quality `0.634` versus `0.473`, role macro F1 `0.675` versus `0.501`, direction macro F1 `0.523` versus `0.436`, and forecast eligibility F1 `0.830` versus `0.394`. It remains a research candidate because ticker recall and issuer-history recall regress, rare extraction decisions remain unresolved, and no independent external holdout has certified it.

The recommended architecture remains layered: deterministic structure and identity invariants, a calibrated classifier for inexpensive first-pass semantics after external validation, and an LLM path for high-value or ambiguous documents. The benchmark does not support replacing every layer with one model.

## Evaluation contracts

### Frozen 100-article benchmark

The model comparison uses the same 100 articles selected from the 1,000-article human-reviewed collection. It contains 394 issuer-scoped truth units. Missing or invalid model outputs remain failures on the full 100-article denominator; they are not silently removed.

Two prompt cohorts exist:

1. **Exact typed prompt V3**: V5 rules, GPT-OSS 20B, GPT-OSS 120B, Qwen3.5-35B-A3B, and Mistral Small 3.1 24B.
2. **Prompt V1 post-hoc revalidation**: the seven OpenAI models. Stored outputs were migrated to the corrected candidate key and revalidated, but the requests were not rerun with prompt V3. These rows are informative, not a strict head-to-head prompt-V3 ranking.

The V5 row is recomputed with the current production rule authority on the exact frozen population. V5 is deterministic: identical source text, metadata, identities, and code produce identical labels.

### V6 sealed classifier holdout

V6 was fit on 588 articles and calibrated on 194 articles. Its official comparison uses the untouched 218-article holdout. The frozen 100 intersects the training collection, so an all-100 V6 score would be contaminated and is intentionally excluded from the model ranking.

### Metric meaning

`Quality` is the unweighted mean of these nine contract metrics:

1. extraction-decision macro F1;
2. ticker-scope F1;
3. content-role macro F1;
4. source-origin macro F1;
5. semantic-direction macro F1;
6. canonical event-concept-family F1;
7. forecast-trigger eligibility F1;
8. reaction-evaluation eligibility F1;
9. issuer-history eligibility F1.

The ordinary `Extraction F1` is also shown, but is not added separately to Quality because the multiclass extraction-decision score is the contract metric. Concept scoring uses the documented canonical-family projection; it does not rewrite the human labels.

## Frozen 100: complete top-level comparison

| System | Contract | Valid | Quality | Extraction F1 | Decision F1 | Ticker F1 | Concept F1 | Role F1 | Origin F1 | Direction F1 | Forecast F1 | Reaction F1 | History F1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V5 deterministic rules | Exact V3 population | 100/100 | 0.391 | 0.920 | 0.184 | 0.657 | 0.321 | 0.348 | 0.253 | 0.400 | 0.193 | 0.193 | 0.974 |
| Qwen3.5-35B-A3B | Exact prompt V3 | 99/100 | 0.493 | 0.944 | 0.189 | 0.779 | 0.495 | 0.504 | 0.472 | 0.544 | 0.600 | 0.153 | 0.696 |
| GPT-OSS 120B | Exact prompt V3 | 97/100 | 0.479 | 0.929 | 0.283 | 0.770 | 0.529 | 0.611 | 0.267 | 0.448 | 0.190 | 0.274 | 0.941 |
| Mistral Small 3.1 24B | Exact prompt V3 | 90/100 | 0.479 | 0.879 | 0.176 | 0.741 | 0.379 | 0.607 | 0.283 | 0.520 | 0.342 | 0.354 | 0.909 |
| GPT-OSS 20B | Exact prompt V3 | 99/100 | 0.361 | 0.955 | 0.227 | 0.815 | 0.200 | 0.461 | 0.213 | 0.429 | 0.209 | 0.067 | 0.631 |
| GPT-5.6 Sol | Prompt V1 revalidation | 100/100 | 0.694 | 0.955 | 0.471 | 0.760 | 0.553 | 0.693 | 0.583 | 0.776 | 0.795 | 0.795 | 0.822 |
| GPT-5.6 Terra | Prompt V1 revalidation | 99/100 | 0.644 | 0.965 | 0.413 | 0.752 | 0.598 | 0.674 | 0.561 | 0.592 | 0.767 | 0.767 | 0.674 |
| GPT-5.4 Mini | Prompt V1 revalidation | 99/100 | 0.594 | 0.930 | 0.299 | 0.749 | 0.522 | 0.702 | 0.529 | 0.669 | 0.552 | 0.374 | 0.950 |
| GPT-5.6 Luna | Prompt V1 revalidation | 97/100 | 0.586 | 0.949 | 0.318 | 0.707 | 0.517 | 0.685 | 0.499 | 0.630 | 0.537 | 0.527 | 0.852 |
| GPT-4.1 Mini | Prompt V1 revalidation | 97/100 | 0.503 | 0.955 | 0.252 | 0.796 | 0.602 | 0.521 | 0.487 | 0.547 | 0.221 | 0.166 | 0.931 |
| GPT-5.4 Nano | Prompt V1 revalidation | 99/100 | 0.477 | 0.943 | 0.194 | 0.787 | 0.516 | 0.582 | 0.606 | 0.641 | 0.396 | 0.087 | 0.481 |
| GPT-4.1 Nano | Prompt V1 revalidation | 95/100 | 0.391 | 0.924 | 0.210 | 0.749 | 0.364 | 0.278 | 0.278 | 0.371 | 0.241 | 0.196 | 0.835 |

## Performance by content role

Values are per-class F1. A zero means the class was not recovered, not that the class was absent.

| System | Analyst | Automated | Editorial | Roundup | Mover recap | Preview | Primary | Regulatory | Why-moving |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.769 | 0.000 | 0.350 | 0.000 | 0.000 | 0.621 | 0.308 | 0.636 | 0.444 |
| Qwen3.5-35B-A3B | 0.815 | 0.000 | 0.545 | 0.333 | 0.467 | 0.839 | 0.605 | 0.333 | 0.600 |
| GPT-OSS 120B | 0.870 | 0.286 | 0.583 | 0.480 | 0.615 | 0.774 | 0.667 | 0.625 | 0.600 |
| Mistral Small 3.1 24B | 0.818 | 0.000 | 0.421 | 0.696 | 0.632 | 0.909 | 0.632 | 0.632 | 0.727 |
| GPT-OSS 20B | 0.727 | 0.000 | 0.353 | 0.467 | 0.727 | 0.741 | 0.533 | 0.600 | 0.000 |
| GPT-5.6 Sol | 0.957 | 0.286 | 0.667 | 0.846 | 0.700 | 0.875 | 0.667 | 0.333 | 0.909 |
| GPT-5.6 Terra | 0.846 | 0.364 | 0.667 | 0.769 | 0.824 | 0.839 | 0.622 | 0.333 | 0.800 |
| GPT-5.4 Mini | 0.870 | 0.545 | 0.333 | 0.533 | 0.824 | 0.875 | 0.762 | 0.667 | 0.909 |
| GPT-5.6 Luna | 0.880 | 0.444 | 0.526 | 0.727 | 0.700 | 0.839 | 0.681 | 0.571 | 0.800 |
| GPT-4.1 Mini | 0.800 | 0.400 | 0.444 | 0.538 | 0.250 | 0.759 | 0.533 | 0.462 | 0.500 |
| GPT-5.4 Nano | 0.846 | 0.000 | 0.353 | 0.348 | 0.625 | 0.875 | 0.667 | 0.800 | 0.727 |
| GPT-4.1 Nano | 0.727 | 0.222 | 0.133 | 0.421 | 0.000 | 0.000 | 0.377 | 0.333 | 0.286 |

Important role findings:

- Analyst events are the easiest role for nearly every system.
- Automated summaries are unresolved: V5, Qwen, Mistral, and GPT-OSS 20B have F1 `0.000`; the best observed value is only `0.545` from GPT-5.4 Mini.
- V5 completely misses market roundups and mover recaps. This directly explains why those documents can be treated as primary triggers by downstream consumers.
- Regulatory and primary-event discrimination remains imperfect even for the strongest LLMs; these two roles should not be collapsed.

## Performance by source origin

| System | Analyst research | Automated | Editorial aggregation | Editorial original | Issuer direct | Regulatory primary |
|---|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.435 | 0.182 | 0.256 | 0.489 | 0.154 | 0.000 |
| Qwen3.5-35B-A3B | 0.519 | 0.513 | 0.533 | 0.444 | 0.706 | 0.118 |
| GPT-OSS 120B | 0.444 | 0.312 | 0.245 | 0.105 | 0.275 | 0.222 |
| Mistral Small 3.1 24B | 0.308 | 0.190 | 0.000 | 0.448 | 0.600 | 0.154 |
| GPT-OSS 20B | 0.455 | 0.375 | 0.000 | 0.059 | 0.222 | 0.167 |
| GPT-5.6 Sol | 0.632 | 0.364 | 0.685 | 0.654 | 0.667 | 0.500 |
| GPT-5.6 Terra | 0.667 | 0.400 | 0.561 | 0.545 | 0.696 | 0.500 |
| GPT-5.4 Mini | 0.588 | 0.519 | 0.568 | 0.465 | 0.750 | 0.286 |
| GPT-5.6 Luna | 0.632 | 0.348 | 0.385 | 0.563 | 0.667 | 0.400 |
| GPT-4.1 Mini | 0.500 | 0.611 | 0.383 | 0.423 | 0.606 | 0.400 |
| GPT-5.4 Nano | 0.706 | 0.621 | 0.548 | 0.577 | 0.783 | 0.400 |
| GPT-4.1 Nano | 0.455 | 0.308 | 0.208 | 0.118 | 0.357 | 0.222 |

Source origin is one of the weakest local-model dimensions. Qwen is the only exact-V3 local model with balanced nontrivial performance across aggregation, original editorial, and issuer-direct origins. Regulatory-primary evidence is sparse and remains poorly recovered by every model.

## Performance by semantic direction

| System | Mixed | Negative | Neutral | Positive | Macro F1 |
|---|---:|---:|---:|---:|---:|
| V5 rules | 0.302 | 0.583 | 0.539 | 0.177 | 0.400 |
| Qwen3.5-35B-A3B | 0.367 | 0.773 | 0.295 | 0.742 | 0.544 |
| GPT-OSS 120B | 0.267 | 0.768 | 0.090 | 0.669 | 0.448 |
| Mistral Small 3.1 24B | 0.298 | 0.804 | 0.366 | 0.610 | 0.520 |
| GPT-OSS 20B | 0.195 | 0.743 | 0.199 | 0.577 | 0.429 |
| GPT-5.6 Sol | 0.551 | 0.900 | 0.758 | 0.896 | 0.776 |
| GPT-5.6 Terra | 0.386 | 0.884 | 0.248 | 0.848 | 0.592 |
| GPT-5.4 Mini | 0.333 | 0.822 | 0.706 | 0.815 | 0.669 |
| GPT-5.6 Luna | 0.521 | 0.880 | 0.296 | 0.823 | 0.630 |
| GPT-4.1 Mini | 0.519 | 0.758 | 0.202 | 0.711 | 0.547 |
| GPT-5.4 Nano | 0.486 | 0.714 | 0.557 | 0.807 | 0.641 |
| GPT-4.1 Nano | 0.108 | 0.627 | 0.159 | 0.589 | 0.371 |

V5's positive F1 of `0.177` is the clearest production-rule defect. It predicts neutral for 102 of the 130 positive issuer units in this benchmark. GPT-OSS models also struggle with neutral and mixed language. Sol is the only evaluated system with F1 above `0.75` for negative, neutral, and positive simultaneously.

## Performance by extraction decision

The taxonomy includes `labeled`, `identity_not_found`, `no_supported_event`, `non_issuer_market_content`, `passage_ambiguous`, and `unsupported_instrument`.

| System | Identity missing | Labeled | No event | Non-issuer | Ambiguous | Unsupported |
|---|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.000 | 0.920 | 0.000 | 0.000 | 0.000 | 0.000 |
| Qwen3.5-35B-A3B | 0.000 | 0.944 | 0.000 | 0.000 | 0.000 | 0.000 |
| GPT-OSS 120B | 0.200 | 0.929 | 0.286 | 0.000 | 0.000 | 0.000 |
| Mistral Small 3.1 24B | 0.000 | 0.879 | 0.000 | 0.000 | 0.000 | 0.000 |
| GPT-OSS 20B | 0.182 | 0.955 | 0.000 | 0.000 | 0.000 | 0.000 |
| GPT-5.6 Sol | 0.000 | 0.955 | 0.250 | 0.750 | 0.000 | 0.400 |
| GPT-5.6 Terra | 0.000 | 0.965 | 0.500 | 0.600 | 0.000 | 0.000 |
| GPT-5.4 Mini | 0.000 | 0.930 | 0.200 | 0.667 | 0.000 | 0.000 |
| GPT-5.6 Luna | 0.000 | 0.949 | 0.308 | 0.333 | 0.000 | 0.000 |
| GPT-4.1 Mini | 0.000 | 0.955 | 0.308 | 0.000 | 0.000 | 0.000 |
| GPT-5.4 Nano | 0.000 | 0.943 | 0.222 | 0.000 | 0.000 | 0.000 |
| GPT-4.1 Nano | 0.000 | 0.924 | 0.125 | 0.000 | 0.000 | 0.000 |

High binary extraction F1 hides this failure: nearly every system learns to label most documents but fails to distinguish the rare reasons for abstention. The macro decision score correctly penalizes that behavior.

## Eligibility precision, recall, and F1

| System | Forecast P | Forecast R | Forecast F1 | Reaction P | Reaction R | Reaction F1 | History P | History R | History F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.130 | 0.371 | 0.193 | 0.130 | 0.371 | 0.193 | 0.984 | 0.964 | 0.974 |
| Qwen3.5-35B-A3B | 0.720 | 0.514 | 0.600 | 0.086 | 0.714 | 0.153 | 1.000 | 0.534 | 0.696 |
| GPT-OSS 120B | 0.214 | 0.171 | 0.190 | 0.173 | 0.657 | 0.274 | 1.000 | 0.889 | 0.941 |
| Mistral Small 3.1 24B | 0.222 | 0.743 | 0.342 | 0.232 | 0.743 | 0.354 | 1.000 | 0.832 | 0.909 |
| GPT-OSS 20B | 0.127 | 0.600 | 0.209 | 0.044 | 0.143 | 0.067 | 1.000 | 0.461 | 0.631 |
| GPT-5.6 Sol | 0.763 | 0.829 | 0.795 | 0.763 | 0.829 | 0.795 | 1.000 | 0.698 | 0.822 |
| GPT-5.6 Terra | 0.737 | 0.800 | 0.767 | 0.737 | 0.800 | 0.767 | 1.000 | 0.508 | 0.674 |
| GPT-5.4 Mini | 0.414 | 0.829 | 0.552 | 0.242 | 0.829 | 0.374 | 1.000 | 0.905 | 0.950 |
| GPT-5.6 Luna | 0.397 | 0.829 | 0.537 | 0.387 | 0.829 | 0.527 | 1.000 | 0.742 | 0.852 |
| GPT-4.1 Mini | 0.127 | 0.857 | 0.221 | 0.092 | 0.857 | 0.166 | 1.000 | 0.871 | 0.931 |
| GPT-5.4 Nano | 0.321 | 0.514 | 0.396 | 0.182 | 0.057 | 0.087 | 1.000 | 0.317 | 0.481 |
| GPT-4.1 Nano | 0.178 | 0.371 | 0.241 | 0.111 | 0.857 | 0.196 | 1.000 | 0.716 | 0.835 |

Eligibility must not be inferred from role or direction alone. Qwen's forecast gate is useful, but its reaction-evaluation precision is only `0.086`. V5's forecast/reaction precision is `0.130`, which means most units it marks eligible are false positives under the human contract.

## Deterministic V5 versus calibrated V6 classifier

This is the fair classifier comparison on the sealed 218-article holdout.

| Metric | V5 rules | V6 classifier | Change |
|---|---:|---:|---:|
| Quality | 0.473 | 0.634 | +0.160 |
| Extraction F1 | 0.943 | 0.946 | +0.003 |
| Extraction-decision macro F1 | 0.189 | 0.189 | +0.001 |
| Ticker-scope F1 | 0.564 | 0.687 | +0.122 |
| Canonical concept F1 | 0.363 | 0.490 | +0.127 |
| Content-role macro F1 | 0.501 | 0.675 | +0.174 |
| Source-origin macro F1 | 0.427 | 0.647 | +0.220 |
| Direction macro F1 | 0.436 | 0.523 | +0.088 |
| Forecast eligibility F1 | 0.394 | 0.830 | +0.436 |
| Reaction eligibility F1 | 0.394 | 0.830 | +0.436 |
| Issuer-history eligibility F1 | 0.990 | 0.831 | -0.159 |

### Holdout role F1

| System | Analyst | Automated | Editorial | Roundup | Mover recap | Preview | Primary | Regulatory | Why-moving |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.812 | 0.737 | 0.207 | 0.000 | 0.385 | 0.766 | 0.646 | 0.400 | 0.556 |
| V6 classifier | 0.800 | 0.429 | 0.516 | 0.806 | 0.837 | 0.649 | 0.807 | 0.615 | 0.615 |

### Holdout direction F1

| System | Mixed | Negative | Neutral | Positive | Macro F1 | Accuracy |
|---|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.336 | 0.539 | 0.526 | 0.341 | 0.436 | 0.468 |
| V6 classifier | 0.468 | 0.697 | 0.263 | 0.665 | 0.523 | 0.491 |

### Holdout eligibility behavior

| System | Forecast P | Forecast R | Forecast F1 | History P | History R | History F1 |
|---|---:|---:|---:|---:|---:|---:|
| V5 rules | 0.277 | 0.684 | 0.394 | 0.991 | 0.989 | 0.990 |
| V6 classifier | 0.825 | 0.835 | 0.830 | 1.000 | 0.711 | 0.831 |

V6's gains are real on the sealed holdout, but its trade-offs are not optional details:

- ticker recall falls from `0.988` to `0.703` even while precision rises;
- neutral-direction F1 falls from `0.526` to `0.263`;
- issuer-history recall falls from `0.989` to `0.711`;
- extraction-decision macro F1 does not improve because rare abstention reasons remain effectively unlearned;
- regulatory-primary origin F1 is `0.000` on the holdout.

V6 should therefore not replace V5 globally. Its forecast gate and semantic predictions are promising, while structural identity, abstention, and history retention still require explicit authority and external validation.

## Reliability, speed, and monetary cost

| System | Contract | Valid | Observed articles/min | API cost for 100 | Operational note |
|---|---|---:|---:|---:|---|
| V5 rules | Exact frozen population | 100/100 | 1,096.6 | $0 | One laptop CPU timing pass; no model load |
| V6 classifier | Indicative frozen-population timing | 100/100 | 3,438.3 | $0 | 0.43 s model load plus 1.75 s inference; not its official evaluation population |
| GPT-OSS 20B | Exact prompt V3 | 99/100 | 64.0 | $0 API | Local GPU compute cost not measured |
| Qwen3.5-35B-A3B | Exact prompt V3 | 99/100 | 33.1 | $0 API | One context-overflow failure |
| GPT-OSS 120B | Exact prompt V3 | 97/100 | 33.1 | $0 API | Three invalid/missing outputs |
| Mistral Small 3.1 24B | Exact prompt V3 | 90/100 | 3.15 | $0 API | One context overflow and nine output-budget truncations |
| GPT-5.6 Sol | OpenAI Batch prompt V1 | 100/100 | 44.8 batch throughput | $1.0841 | Strongest quality; not live latency |
| GPT-5.6 Terra | OpenAI Batch prompt V1 | 99/100 | 49.6 batch throughput | $0.4868 | One invalid/missing output |
| GPT-5.4 Mini | OpenAI Batch prompt V1 | 99/100 | 50.8 batch throughput | $0.1660 | One invalid/missing output |
| GPT-5.6 Luna | OpenAI Batch prompt V1 | 97/100 | 6.32 batch throughput | $0.2098 | Three invalid/missing outputs |
| GPT-4.1 Mini | OpenAI Batch prompt V1 | 97/100 | 8.52 batch throughput | $0.0733 | Three invalid/missing outputs |
| GPT-5.4 Nano | OpenAI Batch prompt V1 | 99/100 | 40.5 batch throughput | $0.0433 | One invalid/missing output |
| GPT-4.1 Nano | OpenAI Batch prompt V1 | 95/100 | 54.1 batch throughput | $0.0177 | Five invalid/missing outputs |

OpenAI Batch throughput is job-level completion throughput, not per-article online latency. Local API cost of zero excludes electricity, GPU occupancy, download time, and operator time. Deterministic timings are single laptop measurements and should be treated as order-of-magnitude evidence rather than a formal benchmark.

## Recommended use by responsibility

| Responsibility | Current evidence-based choice | Reason |
|---|---|---|
| Structural parsing, timestamps, source preservation, candidate identity | Deterministic authority | Integrity-critical and reproducible |
| Current production fallback | V5 rules with explicit pending/uncertain states | Fast and deployed, but known semantic limits must remain visible |
| Historical high-quality teacher | GPT-5.6 Sol | Best broad semantic and eligibility performance observed |
| Local high-quality teacher | Qwen3.5-35B-A3B | Best exact-V3 local quality and strongest local source-origin balance |
| Cheap semantic classifier candidate | V6 after external validation and authority repair | Large holdout gains at CPU speed, but known recall and rare-class defects |
| Ambiguous/high-value live documents | LLM escalation | Rule/classifier uncertainty should route rather than silently decide |

Before any production cutover:

1. build a new external collection not used to fit or calibrate V6;
2. rerun Sol and selected OpenAI candidates with the exact prompt-V3 contract if strict cross-provider ranking is required;
3. repair V6 ticker recall, neutral direction, issuer-history recall, regulatory-primary origin, and rare extraction-decision handling;
4. calibrate confidence per label family rather than exposing raw class probability as universal confidence;
5. keep source metadata, identity, evidence spans, and abstention reasons outside model discretion.

## Reproducibility references

Durable code:

- Production rule rerun: `research/text_intelligence/semantic_calibration_v1/run_compare_v5.py`
- V5/V6 evaluation: `research/text_intelligence/semantic_calibration_v1/comparison.py`
- V6 classifier: `research/text_intelligence/semantic_calibration_v1/news_v6.py`
- V6 fit/evaluation launcher: `research/text_intelligence/semantic_calibration_v1/run_fit_news_v6.py`
- OpenAI benchmark and quality-score authority: `research/text_intelligence/semantic_calibration_v1/openai_gold_benchmark.py`
- Local vLLM benchmark: `research/text_intelligence/semantic_calibration_v1/oss_gold_benchmark.py`

Runtime evidence is intentionally outside the repository:

- Ground truth and V5/V6 results: `D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000`
- OpenAI revalidation: `D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\gold_candidate_revalidation_v2`
- Frozen 100 bundle: `D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\oss_gold_100_v3\shared`
- Local model results: `\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\text_intelligence\semantic_calibration_v1\oss_gold_100_v3\models`

The report values were recomputed or read from those authorities on 2026-08-01. Generated predictions, logs, manifests, metrics, and model artifacts remain in runtime storage; only this requested durable interpretation is stored in the source repository.
