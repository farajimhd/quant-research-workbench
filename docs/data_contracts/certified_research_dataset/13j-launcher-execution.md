# Unified launcher: execution and resume

[Controller](13-campaign-controller.md) · [Capacity contract](13c-plan-capacity.md)

## Available path

`scripts/prepare_certified_research.py` is the entry point for schema inspection,
installation, source preparation, plan inspection, status, execution/resume and
read-only verification. `run` and `resume` execute the same **installed immutable
plan**; verified receipts control reuse. They do not replace stored dates,
workers, memory limits, rules or recipes with current CLI defaults.

| Command | Required input | Result |
|---|---|---|
| `prepare-sources` | End date; optional requirement-derived context start | Existing updater/import and canonical reporting prerequisite checks |
| `launch` | New dataset UUID, explicit source/SEC context bounds, qualified capacity profile; optional end date defaults to latest canonical source | Schema checks/install, canonical preparation, retained-reference discovery, context assembly and admitted initial execution |
| `stored-plan` | Dataset UUID | Read-only plan and resource inspection |
| `run` / `resume` | Dataset UUID and qualified capacity profile path | Admission, bounded dependency execution, receipt reuse and seal |
| `status` | Dataset UUID | Persisted progress inspection |
| `verify` | Dataset UUID | Independent read-only authority and seal verification |

Use `--format json` for machine-readable terminal output. Text execution reports
queued, active, certified, reused, blocked, failed and interrupted counts at most
once per second. Ctrl+C requests stop and drains active work; resume uses the
same UUID. Failures return 2, interrupted execution 130, successful execution 0.
Claims and clients close on every outcome; signal handling is restored.

Acquisition flags belong to `prepare-sources`, before freezing a plan. The
Massive subscription is disabled: no provider probes or download tests are
required. The existing `--acquire-missing` downloader path remains available for
resubscription, with optional `--stage-archive` G-to-D staging for new imports.

## Authorized retained-file reporting recovery

`prepare-sources --recover-retained-reporting` enables the explicitly approved
ingestion migration for existing imported days. It uses retained trade files;
it performs no REST request or provider download. Derivation consumers continue
to read canonical events and certified sidecars exclusively.

The launcher checks actual D-volume headroom before planning, before each chunk,
and before and during copies. Defaults reserve the larger of **256 GiB or 10% of
D capacity**, with a **256 GiB maximum chunk**. Customize with
`--staging-reserve-gib`, `--staging-reserve-fraction`, and
`--staging-chunk-gib`; a zero chunk bound permits all available headroom.
Files are processed sequentially inside these bounds. G originals and existing
D originals are preserved. Private D copies are hash verified and removed only
after original-clock reconciliation and native reporting certification.

The sizing pass reads the staged trade gzip; the extraction pass materializes
it into an explicit compact, losslessly compressed `live_market_ssd` temporary
table. Sixteen ticker groups reconcile in ClickHouse against canonical fields
and ordinals, using bounded query memory and external sorting. Missing rows,
field differences and ambiguous ordering block publication. The full original
condition array and nanosecond clocks remain in the certified sidecar. Native
repair changes only reporting bits and independently verifies preserved fields.
Counts-only legacy reporting receipts do not qualify as original-clock proof.

Exact temporary and receipt schemas are defined by `raw_table()` and
`receipt_table()` in `pipelines/market_sip/flatfiles/retained_reporting.py`;
the durable original-clock schema is `reporting_evidence.ddl()`. Every column
has a lossless codec and all tables require `live_market_ssd`. Existing table
column types and actual part placement are checked before raw staging writes.
Interrupted captures retain reconciliation state. A frozen capture receipt can
resume native repair without reopening an original file. Conflicting evidence
blocks reuse.

October 13 metadata inspection found 189 January–October 2 trade files totaling
597,054,672,991 bytes: 179 on G and ten already on D. This is compressed input
size, **not** a certified sidecar/output storage projection. The bounded real
ClickHouse integration test proves reconciliation, flag repair, certification,
preserved prices and receipt reuse; full-day throughput is not yet qualified.

## SEC acquisition admission

SEC producers require completed, error-free acquisition and integrity coverage
for their actual context range, independently of fact fingerprints. See
[SEC acquisition admission](13k-sec-acquisition-admission.md) for the exact rules,
current August 13 exclusive boundary, and validation evidence.

## Feature selection

`--resolutions-ms` selects persisted bars and continuous indicator-state frames;
1s is required for causal V7. SQL rollups calculate only selected resolutions
and their necessary temporary intermediates. The 100ms execution/liquidity
authority remains required. The default selects all eight supported frames.
`--volume-windows-seconds` and `--short-windows-sessions` select their respective
rolling features and determine their context requirements. Resolution selection
is part of the state recipe; incompatible prior frame sets cannot be reused.
A bounded two-session ClickHouse test verifies selected bars/indicators, all
three V7 checkpoints, price-empty state continuity, discovery and receipt reuse.

## Capacity profile configuration

This is small launch **configuration**, not a market/reference data export. All
products still publish only to ClickHouse. Keep operational configurations under
the runtime root. Profile loading checks integrity and compatibility; it does
not independently qualify measurements. Production measurement qualification
remains unfinished and no fabricated profile is supplied.

The JSON object has these exact fields:

| Field | Contract |
|---|---|
| `version` | `qualified-capacity-profile-v1` |
| `installation_hash` | Current `installation.manifest(database)` fingerprint |
| `reference_contract_hash` | `digest(runtime_plan.reference_contract())` |
| `rates` | 1–64 unique `PilotRate` records; measured units, persisted/staging/merge bytes, peak concurrency, policy and evidence hash |
| `reserve_bytes` | Explicit nonnegative reserves for both workload policies |
| `volume_groups` | Physical-volume grouping for both workload policies; shared volumes share a group |
| `safety_factor` | Finite decimal string, at least one; normal capacity default is 1.25 |
| `profile_hash` | `digest` of all preceding fields |

Each rate's `recipe_hash` binds
`digest({'market_recipe': frozen_market_recipe_hash, 'reference_contract': reference_contract_hash})`.
Actual disk headroom, whole-plan product demands, bounded concurrency and
shared-volume totals are checked again immediately before execution. Missing,
incompatible or unclassified rates block writes. Profiles are limited to 1 MiB;
duplicate JSON fields, nonfinite numbers and fingerprint changes are rejected.

## Initial campaign boundary

`initial_plan.assemble` joins certified source roots, frozen recipe chunks,
prepared state lanes, price-only historical listings and reference prerequisites.
`initial_plan.run` publishes and executes through the same admission gates.
These APIs have focused and bounded ClickHouse tests.

[First-launch preparation](13l-initial-preparation.md) now connects SEC float
measurement-price discovery, expanded canonical/reporting context, dated
membership, compatible state discovery and price-only histories to the assembler.
It remains read-only and requires supplied reference source locators.

`launch` connects the first-launch path separately from stored `run/resume`.
It requires `--context-start-date` and `--fundamental-start-date`; these are
explicit available-history bounds, not proof of adequate context. Preparation
derives the actual intervening sessions and float-price requirements within
the certified history. Missing earlier evidence still blocks.

Acquisition and retained-reporting controls are supported on `launch`, with
the same disabled-by-default provider acquisition and SSD reserve settings as
`prepare-sources`. Existing dataset definitions reject `launch` and require
`resume`; claims and signal handlers close/restore on every outcome. The launch
does not publish a partial plan when required reference authorities are missing.

Prior V7 candidates get independently resolved corporate-adjustment hashes;
their stored hashes are not adopted as evidence. Compatible state discovery
still verifies concrete recipe/source/receipt bindings. Missing initial genesis,
legacy qualification or rename bridges remain blockers. Creating missing
reference authorities and measuring production capacity remain unfinished.

The bounded real CLI resume test supplies source/runtime ownership and capacity
fixture seams. It proves stored-plan execution wiring, readback and cleanup;
it does not certify production source completeness or storage measurements.
