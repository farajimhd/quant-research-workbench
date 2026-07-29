# Issuer-Scoped News and SEC Intelligence V4

This package implements the eight-stage authority used to organize News and
SEC evidence without replacing or duplicating canonical rendered text.

## Eight stages

1. **News structure extraction** parses provider-body structure once and
   excludes later external enrichment from semantic ownership.
2. **News issuer/event scoping** resolves article-local exchange symbols,
   point-in-time aliases, headings, subjects, and relational event
   participants. Provider ticker arrays remain retrieval hints.
3. **Issuer-specific News classification** gives every directly affected
   issuer access to the complete provider publication while labeling only its
   issuer-scoped evidence. Acquisition, partnership, litigation, and analyst
   comparisons may therefore affect multiple issuers without copying another
   issuer's direction or concepts.
4. **SEC document/section extraction** selects meaningful rendered filing
   sections and excludes administrative, signature, contact, and boilerplate
   blocks.
5. **SEC event classification** labels exact section evidence with filing,
   document, point-in-time issuer, and event provenance.
6. **Certification and persistence** writes five mandatory and five fresh News
   audits plus five SEC runtime audits, checks human-readable expected
   outcomes, and provides a
   dry-run-by-default resumable full-corpus launcher.
7. **Related-content relationships** persists normalized source, unit, event,
   issuer, and concept edges. It does not copy publication or filing text.
8. **Live and downstream consumption** makes News Intelligence use this same
   issuer-scoping authority for live notifications and historical
   reconciliation. Market AI and prior-news context read the resulting V4
   semantic stream.

## Multi-issuer contract

- The canonical rendered article is the publication-text authority.
- Each directly affected issuer receives the same publication hash and can use
  the intact publication as model input.
- `semantic_evidence_text`, `issuer_role`, `event_id`, `event_tickers`, and
  `evidence_scope` are issuer-specific.
- A shared relationship clause is evidence for each explicit participant.
- Independent clauses in a roundup remain separate ticker observations and
  cannot become forecast triggers.
- Incidental or unresolved background entities do not invalidate a resolved
  event. An unresolved direct counterparty is retained as
  `shared_ambiguous`; it is never silently assigned a false ticker.
- Later `Source [external:*]` enrichment stays auditable but cannot introduce
  subjects, labels, or trigger eligibility.
- Semantic direction is synthesized only after issuer role is known. A signed
  acquisition is positive evidence, with an additional target-side premium;
  explicit financing, margin, integration, regulatory, or analyst evidence can
  offset it and produce a mixed or negative issuer result.
- Analyst opinion is a source/relationship classification, not an assumption
  of large market impact. It can be correctly labeled while retaining a modest
  direction weight.
- Automated summaries, mover roundups, and why-moving follow-ups remain
  issuer-specific context but cannot become reaction-evaluation triggers.

## Certification

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_certification
```

Generated evidence is written only to:

```text
<machine runtime root>/text_intelligence/scoped_labeling_v4/certification
```

The exact regression set includes a multi-issuer acquisition/analyst case,
clinical events, an alias-conflict case, a market roundup, SEC guidance and
settlement evidence, preferred-stock/warrant financing, an employee-plan
amendment, historical prospectus context, and an administrative SEC
abstention.

## Full-corpus build

Read-only planning is the default:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist
```

Execution requires the matching clean certification manifest:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist --execute
```

The bounded, resumable worker path creates only new versioned products:

- `q_live.scoped_text_labels_v4`
- `q_live.scoped_content_relations_v2`
- `q_live.scoped_text_labels_v4_build_status`

The label table stores only issuer evidence and the canonical publication hash;
the relationship table stores only graph edges. Canonical rendered News and SEC
tables are never mutated. Work is partitioned by corpus and date window, with
the following operational guarantees:

- CPU-heavy labeling runs in isolated worker processes rather than a Python
  thread pool.
- Each News window applies partition predicates to both latest-row inputs
  before joining, then fully drains that bounded response before CPU labeling.
  This prevents `FINAL` from scanning the multi-year corpus and prevents CPU
  work from holding a ClickHouse formatter socket open.
- SEC filing identities are drained first and grouped by the rendered/document
  tables' physical `cityHash64(cik) % 64` partition. Each partition batch uses
  exact filing keys plus latest-row `LIMIT 1 BY` selection; its response is
  closed before classification. This preserves `FINAL` semantics without a
  corpus-wide merge and bounds worker memory even when a week contains
  gigabytes of rendered filing text.
- News and SEC periods are interleaved, so one corpus cannot starve the other.
- Label and relationship inserts are bounded by serialized bytes as well as
  row count; the defaults cap each payload at 8 MiB.
- Running workers write durable heartbeats and stage timings. The terminal
  prints compact 30-second active summaries and completion lines with source,
  classification, write, total, and ETA evidence.
- A transient ClickHouse stream close, reset, timeout, or truncated
  JSONEachRow response retries the complete bounded period through a fresh
  connection. Partial inserts are safe because label and relationship
  identities use `ReplacingMergeTree`; retrying never advances durable unit
  coverage twice.
- Retries use bounded exponential backoff (six by default) and remain visible
  as one compact `RETRY` line. Deterministic ClickHouse/schema errors are not
  retried and still stop the run.
- Each source query uses one ClickHouse execution thread by default, preventing
  64 Python workers from multiplying into unbounded database-side query
  threads. Override only through
  `SCOPED_LABELING_CLICKHOUSE_MAX_THREADS=1..8`.
- Spawned workers recycle after 32 bounded periods to limit allocator and
  regular-expression state growth during the multi-million-document build.
- Ctrl+C terminates active workers promptly. The parent changes the current
  run's remaining `running`/`retrying` statuses to `interrupted`; completed
  periods remain complete and partial periods replay idempotently next time.

The default eight processes are conservative. Sixteen is the recommended
workstation starting point for the repaired bounded queries:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist `
  --execute --workers 16
```

The supported ceiling is 64 workers, but it is a measured tuning ceiling rather
than a recommended default:

```powershell
python -m research.text_intelligence.scoped_labeling_v1.run_persist `
  --execute --workers 64 --transient-retries 6 --retry-base-seconds 2
```

Increasing workers does not change label semantics, period identity, retry
identity, or resume behavior. It is useful only while CPU remains the
bottleneck; a rising retry rate means ClickHouse or the network is saturated
and fewer workers will finish sooner. SEC batches can contain large rendered
documents, so confirm database memory, worker RSS, and retry rate before moving
above 16. Do not use `--rebuild-completed` merely
to adopt the repaired runner; already completed V4 periods are discovered and
retained automatically.

## Live integration

News Gateway continues to own acquisition and canonical rendering. It sends a
lightweight post-persistence identity notice to Text Intelligence, which
reloads the canonical rendered authority. Text Intelligence:

1. runs V4 scoping once;
2. selects eligible issuer units;
3. independently applies the point-in-time QMD price gate;
4. sends the intact article plus issuer-scoped evidence to the model route;
5. persists one `news_semantic_label_v2` row per issuer unit; and
6. dispatches Market AI independently per issuer.

The idempotency identity includes article, unit, ticker, rendered-text hash, and
V4 labeling version.
