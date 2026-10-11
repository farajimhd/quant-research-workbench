# Identity, timing and shared contracts

[Overview](../certified_research_dataset_design.md) · [Next](02-evidence.md)

Understand the rules every dataset and consumer must share.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Common identity, clock and certification contract](#common-identity-clock-and-certification-contract)
- [A concrete timing example](#a-concrete-timing-example)
- [Identity in plain language](#identity-in-plain-language)
- [Outcome and approved policies](#outcome-and-approved-policies)

## Common identity, clock and certification contract

### Dataset identity and versions

All products bind `dataset_id`, schema/algorithm/policy hashes, canonical source receipts, universe revision, context plan, calendar revision and code commit. Names below describe proposed logical products, not tables already implemented.

### Identity keys and coverage outcomes

Keys: issuer/CIK for issuer facts; security ID for share class; listing ID for exchange/broker scope; symbol intervals for ticker lookup. Never join historical records solely on today's ticker. Certificates enumerate required session × listing × product units, with complete, covered-empty, unavailable, conflicted, blocked and failed outcomes. Missing records are never converted into zero values or empty certificates.

### The five clocks

Times are UTC integer microseconds or timezone-aware DateTime64; session dates/boundaries use America/New_York and a pinned exchange calendar. Keep these distinct:

- Market event availability: canonical SIP clock and ordinal ordering. Delayed participant time is classification evidence, not permission to reorder history.
- Measurement/effective time: what period a fundamental or short position describes.
- Public availability: when the original record could first be used.
- Local ingestion and certification times: operational provenance, not historical knowledge.
- Revision availability: when corrected/restated information supersedes a prior version.

### Date-only evidence and later revisions

Date-only publication evidence must not receive an invented exact timestamp. A documented conservative bound may be certified with precision/reason metadata; otherwise the value is unavailable for intraday use. A later restatement cannot replace an earlier vintage before its publication. Reconstructed research availability is named separately from app-observed/live availability.

### Numeric precision and missing values

Prices retain scaled-integer authority; share sizes and notionals retain source precision. Float32 is a model representation, never the threshold/count authority. Every numeric model field has validity, availability and provenance; invalid values are masked, not silently forward-filled into observed trades.

## A concrete timing example

A filing describes the quarter ending December 31, becomes public on February 10, and is downloaded here on June 1. Research for February 11 can use the original filing; research for January 15 cannot. A March restatement is usable only from its own public availability. This is what the approved public-history policy means.

## Identity in plain language

An issuer is the company; a security is its share class; a listing is where that class trades; a symbol is the ticker used during a particular interval. A ticker rename should not create a new company, while two share classes must not be merged merely because they share an issuer.

## Outcome and approved policies

Produce one immutable, identity-keyed certified market/reference set, with versioned adapters for current RL V6 and vectorized optimization V6/V7. Consumers should load prepared inputs, rather than reconstruct fundamentals, stream yesterday's PM/regular bars to start AH, or repeatedly query events during training/search.

This is an execution dependency design, not a code-development schedule. Common expensive inputs are calculated once, then independent consumers run concurrently. Certification is product-specific: source integrity, causal feature correctness, model-ready compatibility and modeled execution are separate claims. Certified inputs do not certify profits or actual broker fills.

Approved decisions from this conversation:

- Reject unknown/invalid reporting clocks and delayed trades from every affected price, volume, count, VWAP, execution and structural product. Preserve canonical events and rejection evidence.
- Retain the existing greater-than-10-second delayed threshold and explicit delayed-condition rules. No evidence reviewed supports changing the threshold.
- Research availability means original public availability, not when this app downloaded a record. Original vintages and revisions must support that claim.
- Produce causal PM, regular and AH V7 checkpoints; retain retrospective labels separately.
- Derive context from launch parameters and feature requirements. Events back to 2019 and checkpoints from 2025 are potential inputs, not automatically compatible seeds.
- Define the full output catalog and dependencies before implementation. Copy required SIP migration files G to D SSD through the ingestion-owned path.
- Integrate `pipelines/market_sip/flatfiles/download_update_events.py` as the acquisition/encoding/publication stage, so the same launcher manages missing earlier sources and future updates.
- Every durable schema must declare compact encoding, lossless compression, partition/order keys and append/backfill behavior before implementation; dense expanded caches are optional bounded accelerators.

No training, search, sealed evaluation, monitor restart, deletion or deployment is part of preparing this design. Existing unrelated changes and immutable research artifacts remain intact.

[Overview](../certified_research_dataset_design.md) · [Next](02-evidence.md)
