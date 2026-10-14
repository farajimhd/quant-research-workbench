# Batch execution and current readiness

[Conid evidence](13vk-conid-reconciliation.md) · [Design overview](../certified_research_dataset_design.md)

## One execution path

`scripts/prepare_certified_research.py` owns batch preparation. Live Reference
Gateway maintenance remains separate. `plan` reads prerequisites without
writing; `launch` prepares sources and freezes a dataset; `launch-collection`
partitions a complete admitted universe into bounded issuer cohorts. Stored
`run`/`resume` use the original configuration, graph and source hashes.

| Stage | Required input | Prepared output / next consumer |
|---|---|---|
| 1. Candidate identities | Canonical FIGI/SEC/listing graph and a completed conid review | Exact wire ticker → canonical security/listing/conid pool, including unresolved cases |
| 2. Dated eligibility | Candidate identities, listing lifecycle, symbol intervals and qualified history | Compact dated membership; current activity is not projected backward |
| 3. Ingestion and migrations | Existing canonical events, certified ingestion evidence; optional new flatfiles | Strict canonical event authority plus reporting certificates |
| 4. Products | Dated membership and strict events; separate certified SEC/FINRA/corporate inputs | Bars/liquidity once → indicators, causal PM/regular/AH V7, activity; fundamentals, shares, float, short products along their own dependencies |
| 5. Verification | Every required product receipt, source hash and predecessor chain | Independently checked dataset/collection seal |

Stages 1–2 and source preparation can progress independently. Market products
wait for both. SEC/FINRA ingestion can proceed independently of market
aggregation, but security normalization and certified products require dated
identity. The existing global worker/memory budget bounds concurrent producers.
Market rows stay in ClickHouse; V7 reads only its required certified bar stream.
No training, optimizer execution, training shards or dataset exports are produced.

## Select reviewed identities explicitly

The completed review is selected by its SHA256, never an arbitrary latest row.
The reader checks SSD placement, original proof preimages, every decision,
receipt and content fingerprint, then recomputes the review from retained
sources. Changed inputs or a future range outside the receipt require a new
review. The selected hash enters campaign and cohort identities and the frozen
universe prerequisite, and is checked on resumed execution.

Pool construction preserves ticker case, inactive identities and OTC references.
It does not close canonical issues. Dated admission additionally checks exact
ticker/security/conid ownership, remaining dependencies and continuous interval
coverage through the event-backed target bounds. Rejected identities remain
visible; a conid decision cannot silently certify a missing historical interval.
Event symbols absent from the retained provider inventory remain explicit
reference/alias dependencies rather than disappearing from preparation.
Unpinned pre-existing datasets retain their original contract and behavior.

Readiness command for the currently reviewed range:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/prepare_certified_research.py plan `
  --start-date 2026-01-01 --end-date 2026-10-02 `
  --universe-anchor-date 2026-10-14 `
  --conid-review-hash 3c3637cf95823baa44b20e7c6efd4634703d9602ff44a2eb0ac819251077fb3b `
  --workers 2 --query-threads 2 --query-memory-gib 8 --global-memory-gib 16
```

The same selection is accepted by fresh launch and collection commands. Launch
also requires a new dataset UUID, source/fundamental context bounds and an
independently qualified capacity profile. Prior context follows the selected
rolling windows, short windows and compatible state dependencies; inspection of
the target range is not certification of the still-required earlier context.

## Acquisition, storage and checkpoints

Future Massive download/encoding/import remains an explicit `--acquire-missing`
option. Do not exercise it while unsubscribed. No historical SIP REST recovery
is used. Retained original-clock recovery is ingestion-owned through
`--recover-retained-reporting`; no downstream producer opens imported SIP files.
`--stage-archive`, `--staging-reserve-gib`, `--staging-reserve-fraction` and
`--staging-chunk-gib` control G → D staging. The updater checks actual headroom,
keeps the larger configured reserve, certifies and cleans each chunk before
advancing. Canonical SIP uses `sip_raw_ssd`; all operational/derived tables use
`live_market_ssd` with packed/scaled fields, dictionaries and lossless codecs.

`backfill-v7` uses a separate target dataset and certified source plan/bars. It
reuses the causal producer and preceding certified states; retrospective labels
are not a causal seed substitute. Missing strict-policy bars or compatible
states must be rebuilt through the canonical pipeline first.

Event `exchange_primary` contains the trade venue or quote ask venue, while
`exchange_secondary` contains the quote bid venue. These are not a security's
primary listing exchange. The updater's projection and [Massive's trade
documentation](https://massive.com/docs/rest/stocks/trades-quotes) establish this
distinction. In particular, listed stocks can have FINRA/off-exchange prints.
Historical listing eligibility therefore requires dated listing/reference
evidence; a blanket venue filter would drop valid listed-stock activity.

## Confirmed current blockers

October 14 inspection of January 2–October 2 found 189 canonical sessions,
120,294,226,954 events and 25,995,919,023 trades. The merged identity pool had
6,781 supported entities, including 6,008 exact event-backed associations.
Those counts are identity candidates, not certified tradability.

| Dependency | Actual finding | Required action |
|---|---|---|
| Dated membership | 7,145 native interval rejections; additional candidate/dependency rejections, potentially overlapping | Repair retained symbol histories, listing lifecycle and issuer/contract bindings with source evidence; do not infer an IPO from first observed events |
| Strict trade policy | All 189 target sessions lack complete qualified reporting certificates | Ingestion-owned clock/condition reconciliation and native flag migration, including unknown/invalid rejection evidence |
| Short data | 3,143,402 short-volume and 142,612 short-interest rows need publication-time evidence | Qualify original public vintages before causal use |
| SEC/corporate inputs | SEC source/coverage receipts and original corporate vintages incomplete | Verify retained source documents/archives, coverage and availability; then produce certified features |
| Writers | Secure laptop Keeper endpoint/certificates not configured | Use managed workstation-local ownership or configure the existing secure laptop endpoint; no plaintext or lease bypass |
| Output/capacity | New research tables absent; no full frozen demand estimate | Install under ownership, qualify representative storage/peak rates and admit the whole plan before writing |

The launcher reports each stage as prepared, blocked or waiting. Readiness is
not a dataset seal. No full-range generation or certification has completed.

Validation used the real launcher against canonical database metadata, including
all 775 pinned decisions and original proof hashes. The focused suite passed
231 tests; 58 optional ClickHouse checks were initially skipped. Nine selected
isolated ClickHouse checks were then explicitly enabled and passed: physical
DDL/placement, original-clock flags/projection, shared market aggregation and
concurrent consumers, causal checkpoint persistence, short-data causality,
derived formulas, and rejection of incomplete SEC prerequisites. Their fixture
database was removed by teardown. These checks do not certify the full campaign.

Workstation execution now uses an isolated deployment of pushed source over the
existing authenticated SSH connection. Local Keeper session, dictionary claim
and canonical source claim were verified, and all 28 research tables were
installed under `live_market_ssd`. HTTP uses the observed LAN-bound port proxy;
no Keeper listener or firewall rule was changed. The retained-file plan found
all 189 trade backups (597,054,672,991 bytes), with about 3.61 TB free on D,
800,154,503,987 bytes reserved and three chunks capped at 256 GiB.

The initial ingestion canary exposed a positional raw INSERT mismatch:
reporting reason preceded clocks in the updater projection but followed the
conditions array in the staging schema. `raw_insert_sql` now names every input
and target field. An isolated ClickHouse regression verifies exact nanosecond
clocks, all six explicit delayed conditions, zero/invalid clock states and
strict rejection flags; it reads synthetic trades without opening SIP files.
This fixes the insertion contract, not the outstanding full-range certificates.
