# Certification, delivery and open decisions

[Overview](../certified_research_dataset_design.md) · [Previous](10a-capacity.md)

Check acceptance requirements, protected splits and decisions before coding.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Certification and acceptance](#certification-and-acceptance)
- [Decisions before implementation](#decisions-before-implementation)
- [What a delivery certificate means](#what-a-delivery-certificate-means)

## Certification and acceptance

### Certification evidence

Certification requires source counts/hashes and ordinal continuity; source-matching reporting receipts; storage placement; dated identity uniqueness and membership coverage; availability/vintage proof; no duplicate keys; sparse-row validity; frame/bucket boundaries; split basis; predecessor hashes; declared missing/conflict outcomes and output hashes. Product completeness is relative to an enumerated required catalog, never merely successful exit codes.

### Market and checkpoint acceptance

Representative acceptance: dense/sparse/quote-only listings, IPO and symbol transition, split/reverse split, early close, no-trade segment, delayed/unknown/invalid clocks and publication revisions. Compare shared gates across price/volume products and exact activity thresholds. Verify rolled bars against the base aggregate and indicator state across days. Restore at PM/regular/AH and compare continuous V7 state/output hashes; compare new producer and backfill; append future bars and prove earlier causal checkpoints unchanged.

### Reference and adapter acceptance

Check original filing/short-data publication boundaries and conservative date-only handling. Prove unchanged pre-publication values when later revisions arrive. Audit missing identities against dated census and prevent survivorship filtering. Compare new consumer adapters to existing clocks, shapes, masks, rank behavior and independently calculated outputs. Verify offline execution reads against pinned database evidence, including freshness/arrival clocks and passive-target capacity.

### Protected splits and normalization

Immutable splits: preserve existing RL and optimization training/development/sealed authorities. Current RL documentation seals Aug 26/Aug 31/Sep 2; optimization history seals its own validation dates. These assignments are separate, not interchangeable. Common mechanical preparation does not authorize reading sealed labels, computing selection metrics or changing splits. Holdout materialization requires an explicit permitted production protocol and access-controlled role; otherwise record deferred units. Normalization/calibration fits use training only.

### Delivery milestones

Three milestones: (1) common market/reference certification; (2) RL-compatible banks plus separately certified labels if selected, and optimizer-compatible compact/history/cache artifacts; (3) detailed replay/financial qualification in a separately authorized task. Never present milestone 1 as finished model-ready delivery if an adapter dependency is missing.

## Decisions before implementation

1. Historical broker policy remains unresolved: require dated IBKR admission proof, or explicitly accept current validated conid with dated historical listing/security evidence as a research universe. Default is blocked when strict evidence is missing; do not quietly change the policy.
2. Freeze the enumerated metric catalog described above, including whether FTD/Reg SHO/classification are required valid values or certified optional/missing states. Certified unavailable is distinct from a valid value; required consumers decide whether it blocks them.
3. Choose authoritative versus modeled tick/LULD/halt requirements per execution use case. Existing modeled scenarios cannot acquire official status through data certification.
4. Decide whether delivery includes RL hindsight-label regeneration or only feature/execution banks ready for a separately selected label producer. Preserve current approved label math and sealed split unless explicitly revised.
5. Select base dataset plus adapter/cache scope after measured storage planning; full-universe export and current ranked-only research export have very different sizes.

## What a delivery certificate means

A common-data certificate proves the declared source, timing, identity and output rules. An RL/optimization compatibility certificate additionally proves its adapter can load the exact required forms. Neither proves that a strategy is profitable or that modeled fills match live broker execution.

[Overview](../certified_research_dataset_design.md) · [Previous](10a-capacity.md)
