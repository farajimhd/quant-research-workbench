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
