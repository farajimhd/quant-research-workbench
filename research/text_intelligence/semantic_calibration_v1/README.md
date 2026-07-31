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
