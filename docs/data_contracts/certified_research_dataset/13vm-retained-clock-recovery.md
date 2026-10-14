# Retained-clock recovery and canary results

[Batch execution](13vl-batch-execution-readiness.md) · [Design overview](../certified_research_dataset_design.md)

## Workstation execution and repairs

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

The production canary also exposed the launcher's fixed 45-second statement
deadline and full-year scans without monthly pruning. New runs use configurable
`--query-timeout-seconds` (default 900, maximum 3600), frozen in their configuration;
old stored plans keep their existing timeout contract. Exact UTC event-date
bounds now accompany the original SIP bounds during canonical certification,
preserved-field checks and flag mutation. Cross-midnight bounds include both
dates. Complete source counts and independent comparisons still gate publication.

Reconciliation now materializes the canonical day's trade keys and original
fields once into the compressed ingestion-only
`q_live.historical_trade_reporting_ns_v1_retained_canonical` table. The 16 ticker
buckets reuse that snapshot instead of scanning canonical months 16 times.
This temporary table uses `live_market_ssd`, explicit original numeric widths,
dated partitions and lossless codecs, and is cleaned only after day certification.
It is not a downstream market-event authority. Admission reserves four times
the measured raw semantic footprint for staging/publication/merge work before
writing; partial staging remains resumable and has no certified receipt.

Exact original-clock publication also uses the same bounded ticker buckets.
Both `EXCEPT ALL` comparisons remain mandatory; their memory no longer scales
with the entire day. Missing-key insertion scopes the existing bank to that
source date and bucket instead of loading all earlier history. A failed
comparison leaves the receipt absent, blocks flag mutation and never overwrites
conflicting original-clock evidence.

## Certified January 2 canary

The workstation canary completed original-clock recovery and canonical high-bit
repair for January 2, 2026. All 115,017,539 trades passed, with 115,017,539
unique keys and zero mismatches: 114,343,002 timely, 673,622 delayed and 915
in the unknown class. Prices, ordinals and other preserved canonical fields
passed the before/after fingerprint verification. Independent source preflight
confirmed reporting readiness. No other source session or derived dataset
was certified by this canary.

The frozen original-clock evidence hash is
`59964a1d3a21cabf148bb3a5e38b6346b26a08ce1f5f3200ec0f0805b454b40c`.
The reporting coverage receipt is in
`q_live.historical_trade_reporting_coverage_v1`; original ingestion receipts
are in `q_live.historical_retained_reporting_capture_v1`. Native repair marker
`27cc8e7537b64d8c558ea35e60cd84d263adc0882e902e10ec290d757eea38a0`
identifies the completed mutation and preserved-field proof.

Temporary raw, canonical and reconciliation stages were cleaned, the private
D copy was removed, and the G backup was retained. All evidence parts were on
`live_market_ssd`. The 28 research tables have correct definitions but no
prepared derived dataset yet. The canary ran source commit `b7cd18d4b`; latest
source `236d2a57c` additionally bounds CLI deadlines and reduces mutation
status polling/log noise without altering calculations.

## Measured storage and execution limitation

The durable nanosecond sidecar contains 115,017,539 records in
1,085,746,304 compressed bytes: about 9.44 bytes/trade. Applying that observed
rate to 25,995,919,023 target trades gives roughly 245 GB of sidecars, before
safety reserves, temporary stages and derived products. This one-day estimate
is not a full capacity certificate. Bars, indicators, V7 and other products
still require representative normalized-output measurements.

Classic flag mutation rewrote one large monthly January part and took roughly
40 minutes. Repeating it per source day would repeat that cost. A full campaign
therefore needs a separately validated month-batched or patch-update execution
path before making a throughput claim. The active canary mutation was allowed
to finish; it was never cancelled or blindly reissued. No production block-number
settings, patch-update mode, partition layout or server-wide resource settings
were changed.

## Monthly execution

The retained recovery controller now captures and reconciles daily evidence,
publishes an immutable original-clock receipt, and cleans that day's temporary
tables and private D copy. The durable sidecar remains on SSD. At each month
boundary it prepares independent daily repair headers and submits one combined
mutation for the days needing repair. Every daily marker is retained in the
server command, so resume discovers the original mutation instead of issuing
another one. Each day is independently verified and certified after completion.

The batch is limited to 31 distinct dates in one month. Quotes and untargeted
days retain their original flags; all prices, ordinals and other preserved
fields remain subject to the existing before/after checks. An interrupted
capture resumes from its immutable receipt. An interrupted repair retains its
server mutation and fail-closed coverage transition. No patch-update settings
or canonical partition changes are introduced.

The real ClickHouse two-day regression proved a single mutation, both daily
certificates, strict unknown/delayed flags, unchanged prices and idempotent
resume. The retained recovery controller and existing single-day corruption
test also passed. This validates the implementation, not full-month production
throughput or certification of the remaining range.
