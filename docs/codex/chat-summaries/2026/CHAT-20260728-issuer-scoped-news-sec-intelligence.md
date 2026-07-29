# 2026-07-28 - Issuer-scoped News and SEC intelligence

- Related task: `TASK-0153`
- Repository: `D:\TradingCodes\quant-research-workbench`

## Outcome

The earlier scoped-label V2 certification was invalid because it treated a
multi-issuer document as globally unsafe and reviewed structural invariants
rather than issuer-level semantic correctness. The replacement
`scoped_text_labeling_v4` keeps one canonical rendered publication while
separating semantic evidence, role, direction, concepts, and eligibility per
directly affected issuer.

For acquisitions, partnerships, litigation, analyst comparisons, and similar
shared events, every explicit participant receives the intact provider
publication and a common event identity. Deterministic labels are computed only
from the evidence resolved to that issuer. Independent roundup clauses remain
ticker observations and do not become causal triggers. External enrichment,
incidental unresolved entities, and stale aliases cannot globally suppress a
resolved event.

SEC extraction and certification were strengthened at the same time. The exact
regression set now requires settlement, guidance, revenue, preferred-stock and
warrant financing, an employee share-plan amendment, historical non-triggering
context, and administrative abstention.

The V4 correction makes direction issuer-role aware rather than merely adding
document-wide concept weights. Signed acquisitions are positive evidence;
targets receive a transaction-premium adjustment while acquirer-specific
financing, margin, integration, regulatory, and analyst evidence can offset the
transaction. Negated investigations are excluded and explicit regulatory
clearance is positive. Analyst opinion remains correctly classified without
claiming that it has a large expected reaction. Automated summaries, mover
roundups, and why-moving follow-ups are retained as ticker context but cannot
be reaction targets. Reported moves are parsed per ticker, including exchange
symbols, up/down variants, and pre-/after-market sessions.

## Durable implementation

- V4 News issuer/event scoping and V3 article-local alias disambiguation.
- V2 SEC relevant-section extraction with event and issuer provenance.
- V4 deterministic semantic concept authority.
- Exact expected-outcome certification with five mandatory News, five fresh
  News, and five SEC Markdown
  audits under the machine runtime root.
- Dry-run-by-default, bounded, resumable persistence into
  `scoped_text_labels_v4`, `scoped_content_relations_v2`, and the V4 build
  status table.
- Normalized source/unit/event/issuer/concept graph edges; no publication text
  duplication.
- Live News Intelligence uses the same scoping authority and writes one
  `news_semantic_label_v2` result per issuer unit. Market AI and prior-news
  context consume the V2 stream.

## Validation

- 74 targeted unit tests passed.
- Modified Python modules compiled.
- `git diff --check` passed.
- Exact ClickHouse-backed certification passed all 15 audits with zero
  attention items and zero expected-outcome failures.
- Manual review confirmed the five fresh News cases, all mandatory cases, and
  five SEC summaries. The 42-stock roundup recovered all 42 reported moves
  while remaining ineligible as a reaction trigger.
- Read-only persistence planning for 2026-07-01 through 2026-07-08 found 3,301
  News rows and 15,476 SEC rows and performed no writes.

## Operational boundary

No historical V4 backfill was executed. After optional audit review, run:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist --execute
```

## Persistence acceleration follow-up

The first workstation backfill established a real baseline of 28 completed
weekly News periods in roughly 61 minutes with zero failures, but exposed four
systemic costs: whole-response materialization, CPU classification under a
thread pool, small row-count-only writes, and a corpus-major plan that starved
SEC.

The V4 persistence runner now uses bounded Windows-spawn process workers,
streams News rows, and reads SEC through small date-filtered filing batches plus
exact `(cik, accession_number)` rendered-document reads. An incremental
point-in-time CIK cache replaces both the broad SEC rendered-text join and
per-document identity queries. News and SEC periods are interleaved. Inserts
are bounded by serialized bytes and row count, workers publish durable
heartbeats and stage timings, and the plain terminal reports compact active
state plus per-completion source/classification/write time and ETA. Ctrl+C sets
a shared cooperative stop signal; completed periods remain durable and partial
periods replay idempotently.

The live V4 classifier, table identities, label semantics, and completion keys
were not changed. Existing completed periods do not need rebuilding.

## Live gateway and Canvas integration

The post-backfill integration now has one runtime authority. News Gateway and
SEC Gateway send only a lightweight canonical identity notice after durable
rendered-text completion. The existing News Intelligence process now hosts a
shared bounded Text Intelligence queue that reloads canonical News V2 or SEC V3
text, applies `scoped_text_labeling_v4`, and persists both
`scoped_text_labels_v4` and `scoped_content_relations_v2`. Deterministic work is
independent of market hours, live-trading authorization, QMD price gates, and
model availability.

`scoped_text_live_status_v2` binds completion to the exact News rendered hash
or ordered SEC rendered-document-set hash. A canonical anti-join reconciles
missed notices and changed revisions. Eligible News issuer units are forwarded
only after deterministic persistence; a second durable anti-join against
`news_semantic_label_v2` repairs queue saturation or process interruption
without repeating current deterministic rows.

Backend News list and detail APIs now attach product-safe issuer-scoped labels
and a compact summary. All News exposes semantic direction, event class, and
concepts. Ticker News separates eligible event candidates from background and
follow-up context. News Details shows a compact issuer-specific interpretation
with role, evidence scope, source origin, timing, direction score, canonical
concepts, exact evidence, and the three forecast/reaction/history eligibility
flags. Raw database names, paths, and pipeline internals remain outside these
product payloads and views.

Validation covered 72 targeted Python tests, Python compilation, the frontend
TypeScript/Vite production build, deterministic light/default and dark/maximum
scale Canvas captures, and interactive mounting of all three News containers
with no client console errors. The laptop ClickHouse authority was offline, so
real data-connected UI rendering and execution of the reconciliation SQL remain
an explicit operational validation gap. The temporary frontend review server
was stopped and port 5173 was verified closed.

## Historical backfill transport repair

The first optimized workstation resume retained 87 of 1,730 weekly corpus
units, then one News source stream for 2010-10-22 through 2010-10-29 closed
after 797 rows with `IncompleteRead(0 bytes read)`. The parent previously
treated any worker exception as fatal, so that one transient transport failure
terminated the whole pool and left 16 stale `running` heartbeats.

The persistence runner now replays the complete bounded week after only
recognized transient ClickHouse transport failures. Each attempt creates a new
client, partial label and relation writes remain idempotent under their
`ReplacingMergeTree` identities, and durable unit coverage advances only after
completion. Retries are prioritized after bounded exponential backoff and are
reported in compact operator-safe terminal lines. Query or schema errors still
fail immediately, while repeated transport failures fail after the configured
limit instead of looping indefinitely.

The supported ceiling is now 64 worker processes. Source queries default to one
ClickHouse execution thread per worker, in-flight work is bounded to the worker
count, and workers recycle after 32 units. This permits measured CPU scaling
without allowing 64 Python processes to multiply into unrestricted
ClickHouse-side parallelism. Existing completed periods remain authoritative;
resume without `--rebuild-completed`.

## Bounded-source repair

The retry-enabled runner exposed an upstream query defect rather than an
insufficient retry count. The failed 2011-01-28 News week contained 3,580
articles, but every attempt scanned roughly 5.1 million rows and 6.9 GB because
the date predicates sat outside a two-table `FINAL` join. Classification then
kept the ClickHouse response open until the server formatter socket closed.
The SEC exact-filing query had the same structural problem: each 64-filing
batch scanned roughly 10.9 million rows and 26.35 GB through global `FINAL`
subqueries.

The repaired authority:

- applies the News date partition predicate independently to event and rendered
  latest-row inputs before joining;
- drains the bounded News response before classification;
- drains SEC filing identities, groups exact filing keys by the rendered and
  document tables' physical `cityHash64(cik) % 64` partition, and uses bounded
  latest-row `LIMIT 1 BY` selection;
- drains each SEC partition response before classifying that batch, bounding
  memory even when a weekly period contains gigabytes of filing text; and
- changes any current-run `running` or `retrying` statuses to `interrupted`
  after the parent terminates workers on fatal error or Ctrl+C.

Validation against the live ClickHouse authority found:

- the formerly failing News week returned 3,580/3,580 unique articles and
  9,958,098 rendered characters;
- the full repaired write path completed it with zero retries in 205.8 seconds,
  persisting 5,849 labels and 24,093 relations; source acquisition took 1.1
  seconds;
- a representative SEC batch returned 304 documents and 9.1 MB in 1.26
  seconds;
- bounded latest-row SEC results matched partition-bounded `FINAL` exactly for
  53/53 identities and text hashes; and
- 41 stale active statuses from the stopped run were closed as `interrupted`.

The workstation should resume with 16 workers. Sixty-four remains a supported
ceiling, not a default; raising concurrency is justified only while CPU remains
the bottleneck and database memory plus retry rate remain healthy.
