# SEC Synthesis V1

SEC Synthesis V1 deterministically compiles one SEC accession across its filing
envelope, narrative documents, XBRL facts, issuer identity, economic implication,
reconciliation, and product-specific eligibility. It preserves exact source
evidence and keeps semantic interpretation separate from optional manual LLM
review and any later market-reaction model.

Every accession receives an envelope-level synthesis, including submissions
without eligible narrative text. `forecast_trigger` is a deterministic,
fail-closed label: it requires point-in-time ticker identity plus a current
material narrative disclosure or a comparable XBRL transition. The record
stores explicit reasons when eligible and blocking flags when not eligible;
manual LLM review never changes this authority.

The current XBRL Company Facts authority does not retain complete duration and
dimensional context. V1 therefore treats non-annual duration comparisons as
unresolved and records limited comparability instead of inferring a quarter or
dimension. This is a fail-closed boundary, not a claim of complete XBRL
certification.

Historical and live paths must call the same `SecSynthesisEngine` and persist to
`q_live.sec_synthesis_v1`. Generated audits and backfill artifacts belong under
`D:\TradingML\runtimes\text_intelligence\sec_synthesis_v1`.
