# Issuer-News Labels V4

V4 is the evidence-free successor to `llm_issuer_news_labels_v3`.

Breaking changes:

- Removes `evidence_sentence_ids`; inputs no longer require sentence numbering.
- Adds top-level `article_forecast_eligible`.
- Defines article eligibility deterministically as true when at least one issuer
  has `forecast_relevance_probability >= 0.5`.

Fresh V4 annotations must provide every issuer field. The validator has an
explicit `allow_legacy_nulls=True` mode only for converting older authorities
that never certified all V4 fields. Null in a converted record means
"unavailable in the legacy authority," never zero, neutral, or negative.

The first legacy conversion is produced by:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
python -m research.text_intelligence.llm_issuer_labeling_v3.convert_consolidated_gold_v1
```

It reads the confirmed consolidated News Synthesis V1 authority, verifies the
declared source hash, performs no semantic relabeling, and writes only under
`D:\TradingML\runtimes`.
