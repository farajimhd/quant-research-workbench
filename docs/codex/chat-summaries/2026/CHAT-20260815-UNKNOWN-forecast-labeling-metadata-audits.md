# Consolidate Forecast Labels, Audit Metadata Exceptions, and Establish the Model-Mismatch Review Program

- Chat started: Exact start time unavailable; accessible activity and runtime artifacts are dated 2026-08-15 PDT
- Chat ended or last activity: 2026-08-15 17:09:52 PDT
- Summary written: 2026-08-15 17:09:52 PDT
- Chat/task identifier: Unavailable in the accessible context
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; standalone forecast-eligibility and gold-only sentiment authorities under `D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4`
- Related task-history entries: `TASK-0191`
- Source completeness: Partial; the active chat and its runtime artifacts were accessible, but early conversational turns were compacted and the exact chat start metadata was unavailable

## Narrative

The chat began with the user extending the standalone news-labeling program to
2025. The central requirement was semantic: workers had to read each supplied
row and label it from the article information, not apply deterministic filename,
row-number, or keyword patterns. A large packetized campaign produced article
forecast-eligibility labels for all 2025 records and then completed the first
eight months of 2026. Generated packets, worker outputs, manifests, and reports
were kept under `D:\TradingML\runtimes`, separate from repository source. The
resulting population combined 18,144 legacy consolidated-gold articles, 202,223
2025 fast-label articles, and 141,328 January-August 2026 articles: 361,695
unique articles with no overlap among the three source authorities. Sentiment
remained deliberately limited to issuer units from consolidated gold; no
sentiment was inferred for the 2025 or 2026 fast labels.

The first combined authority exposed 1,399 articles whose short-text review was
insufficient. A source audit established that 983 were genuinely title-only in
the revision-bound renderer, while 416 had structured body content. The 2026
subset included 185 body-bearing oversized records and 899 title-only records;
the 2025 subset included 231 body-bearing and 84 title-only records. The audit
also found very long rendered articles, including 186 above 100,000 characters.
This changed the plan: exact revision-bound source restoration or full-text
review could resolve body-bearing cases, but re-querying the same revision could
not manufacture evidence for genuinely title-only articles. Insufficient labels
therefore had to remain explicit rather than be guessed.

The user then focused on provider metadata. Tags and channels appeared highly
informative, and halt-tag exceptions raised concern that some gold labels were
wrong. The work joined the labeled population to original Benzinga metadata,
counted ticker/tag/channel combinations, and compared eligible and ineligible
rates. The user rejected the idea of treating apparent exceptions as proof that
metadata was unreliable; instead, halt-tag exceptions and all other strong
metadata-pattern exceptions had to be manually audited before any deterministic
rule could be trusted. A focused gold metadata audit and a subsequent
1,043-article exception audit used independent blind passes and third-reader
adjudication. These corrections produced successive combined authorities rather
than mutating the original gold or fast-label artifacts.

The comprehensive metadata campaign then expanded beyond the initial exception
families. Deterministic analysis joined all 361,695 labels and metadata exactly,
identified 1,206 qualified patterns, and produced 7,296 unique candidate source
IDs from 25,425 pattern-trigger links. The review program intentionally treated
metadata only as a candidate generator. Semantic workers received article text,
not labels, model outputs, or neighboring worker decisions. Short-text review
covered 8,730 sampled candidates; 4,065 cases advanced to full-text independent
review; 1,012 disagreements received a third reader. Mechanical validation
enforced source membership, ordering, uniqueness, exact evidence containment,
attestations, and reconciliation. The final metadata successor,
`combined_gold_fast_eligibility_sentiment_v4`, applied 3,137 forecast-label
corrections: 2,341 eligible-to-ineligible, 787 ineligible-to-eligible, four
eligible-to-insufficient, and five ineligible-to-insufficient. Twelve three-way
votes remained unresolved and were preserved rather than forced. No metadata
pattern met the stringent proposed auto-rule threshold, so the campaign did not
authorize deterministic metadata labeling. Gold sentiment was copied
byte-for-byte and remained unchanged.

The V4 authority contains 361,695 articles: 139,068 eligible, 221,219
ineligible, and 1,408 `insufficient_short_text`. It includes 3,137 applied
multi-reader metadata corrections and 12 explicit unresolved three-way votes.
The gold-only sentiment table contains 16,983 issuer units across 15,062
articles: 9,698 positive, 4,132 negative, 2,432 mixed, and 721 neutral. These
figures supersede earlier combined-v1 counts; the increase from 1,399 to 1,408
insufficient records came from evidence-based metadata-audit corrections.

The user next requested three Random Forest experiments using a deterministic
within-day 15% holdout: metadata only, TF-IDF text only, and their combination.
The frozen decisive population contained 360,287 articles, with 305,586 train
and 54,701 test records. Metadata-only achieved 86.57% accuracy, 87.44% balanced
accuracy, and 84.12% eligible F1. TF-IDF-only achieved 86.52% accuracy, 87.97%
balanced accuracy, and 84.52% eligible F1. The combined model was strongest:
87.21% accuracy, 88.53% balanced accuracy, 85.19% eligible F1, and 0.9641 ROC
AUC. These were interpolation results under a within-day split, not forward
temporal-generalization evidence.

Because the user did not trust the manual labels, model-label disagreements
became an audit signal rather than automatic corrections. The combined model
disagreed with 35,995 of 360,287 decisive labels, or 9.99%. A prediction-blind
random sample of 100 mismatches followed by a non-overlapping 400 produced 500
manual decisions. The current label was judged wrong in 295 cases and the model
wrong in 205: a 59.0% current-label error estimate with Wilson 95% interval
54.6%-63.2%. Directionally, 22 of 27 sampled current-eligible/model-ineligible
labels were judged wrong, while 273 of 473 current-ineligible/model-eligible
labels were judged wrong. The samples established strong triage value but also
proved that blindly flipping every mismatch would corrupt many correct labels.

The user then requested a control audit of model-label matches. A reproducible
500-article sample used seed `20260817`. The reviewer saw only opaque article
packets and complete rendered text. All decisions were locked and hashed before
controller truth was opened. Manual review confirmed 463 labels and overturned
37, a 7.4% error estimate with Wilson 95% interval 5.4%-10.0%. Twenty-five of
206 current eligible labels were judged ineligible, and 12 of 294 current
ineligible labels were judged eligible. The 2026 sample error rate was 12.2%,
versus 4.8% for 2025 and 3.4% for the small legacy-gold sample. Combining the
mismatch and match samples gave a stratified point estimate of about 45,235
questionable labels, or 12.6% of the decisive population, but this remained an
audit estimate rather than an applied correction count.

At the end of the chat, the user requested a durable handoff. The current V4
labels and gold sentiment were frozen byte-for-byte into
`forecast_eligibility_sentiment_authority_v1`. The new authority also includes
the 35,995-row controller mismatch inventory, all 1,000 single-blind audit
observations, the 3,137-row metadata correction ledger, and 12 unresolved
three-way votes. The prior 500 mismatch and 500 match judgments were preserved
as audit evidence and deliberately not promoted into primary labels because
they were single-reader estimates. `LOAD_MANIFEST.json` is the canonical entry
point and references the exact 1.031 GB rendered-text authority by path and
SHA-256. Validation passed exact article and sentiment counts, unique keys,
source membership, byte-identical upstream copies, controller-label agreement,
and hashes. A repository prompt now gives the next chat the complete blind audit
contract and successor-authority procedure.

## Durable decisions

- Forecast eligibility, issuer sentiment, and later market-reaction targets are
  separate concerns. Forecast review must not manufacture or change sentiment.
- The frozen current label authority is
  `forecast_eligibility_sentiment_authority_v1`; load it through
  `LOAD_MANIFEST.json`, not by selecting a neighboring intermediate version.
- V1 is a frozen audit baseline, not a claim that all labels are correct or
  human-certified. Per-row authority and `human_certified` fields remain
  authoritative.
- Metadata tags, channels, and model output may select audit candidates but are
  not semantic label authority. Workers reviewing mismatches must not see them.
- Never auto-flip model-label mismatches. The random audit found the current
  label wrong 59% of the time and the model wrong 41% of the time.
- Single-reader match/mismatch samples are audit evidence only. Apply production
  corrections only through the correction-grade blind workflow.
- Preserve genuinely insufficient and unresolved cases explicitly. Do not infer
  missing article evidence.
- Runtime artifacts belong under `D:\TradingML\runtimes`; only reusable prompts,
  summaries, source, tests, and task history belong in the repository.
- Do not treat the selectively corrected existing test rows as the sole final
  model benchmark. Establish a fresh independently audited holdout or fully
  audit the test population.

## Delivered outcomes

- Completed the 2025 and January-August 2026 fast forecast-label populations and
  combined them with non-overlapping consolidated gold.
- Audited missing/oversized source-text conditions and retained unresolved
  evidence states honestly.
- Completed gold metadata exceptions and the comprehensive multi-reader
  metadata audit, yielding 3,137 applied corrections with validation.
- Trained and evaluated metadata-only, TF-IDF-only, and combined Random Forests
  on one frozen within-day split.
- Built exact combined-model mismatch and match inventories and completed two
  prediction-blind 500-article audit estimates.
- Finalized the immutable runtime authority at
  `D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_sentiment_authority_v1`.
- Added `docs/codex/FORECAST_ELIGIBILITY_MISMATCH_BLIND_AUDIT_PROMPT.md` as the
  operational prompt for the next chat.

## Unfinished or hanging work

- Current state: 35,495 model-label mismatches have no semantic audit, and 500
  have only one blind reader. Why: the samples established yield before scaling.
  Next action: run the correction-grade workflow in the handoff prompt with one
  blind pass over unreviewed rows, required second passes, and third-reader
  adjudication. Owner: next audit chat. Related task: `TASK-0191`.
- Current state: 12 metadata-audit cases have three different votes. Why: no
  majority existed. Next action: put them through a fresh correction-grade lane
  without exposing prior votes. Owner: next audit chat. Related task:
  `TASK-0191`.
- Current state: 1,408 articles remain insufficient. Why: many are genuinely
  title-only at the frozen revision; others need source restoration or safe
  untruncated full-text review. Next action: treat them as a separate provenance
  and source-reacquisition program, not part of ordinary mismatch flipping.
  Owner: future source-restoration task. Related task: `TASK-0191`.
- Current state: no successor correction authority exists. Why: single-reader
  samples were intentionally not promoted. Next action: after the large blind
  audit, write immutable `forecast_eligibility_sentiment_authority_v2`, preserve
  all votes and evidence, and validate exact 361,695-row membership. Owner: next
  audit chat. Related task: `TASK-0191`.
- Current state: Random Forest results are interpolation estimates. Why: the
  split is random within date and the same model selected mismatch candidates.
  Next action: retrain only after label correction and create a fresh audited
  holdout or fully audit the existing test population. Owner: subsequent model
  experiment. Related task: `TASK-0191`.

## Handoff to the next chat

Read `TASK-0191`, this summary, and
`docs/codex/FORECAST_ELIGIBILITY_MISMATCH_BLIND_AUDIT_PROMPT.md`. Verify the
runtime `LOAD_MANIFEST.json`, `VALIDATION.json`, and hashes before packetizing.
Keep controller truth completely separate from semantic worker packets. Use a
fixed reusable worker pool, bounded restart-safe packets, and runtime-only
outputs. The most important next action is the correction-grade blind audit of
all 35,995 mismatches, beginning with a bounded tranche and preserving V1
immutably. Do not auto-flip labels, expose tags/model probabilities to workers,
change gold sentiment, or claim final generalization from the selectively
reviewed existing test set.
