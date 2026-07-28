# Text Candidate Inventory V1

This read-only research pipeline mines empirical phrase, typed-value, and
n-gram candidates from the certified Benzinga rendered-v2 article authority
and SEC rendered-v3 document authority. It does not modify either source table,
change the production news classifier, or assign semantic labels.

The first pass is deliberately an inventory:

- financial values become typed placeholders such as `<money>`,
  `<share_count>`, `<price_per_share>`, `<percentage>`, `<basis_points>`,
  `<multiple>`, and the fallback `<number>`;
- article/document-level phrase presence is counted instead of treating
  repetition inside one document as independent evidence;
- a bounded Space-Saving inventory keeps memory finite and reports a
  conservative document-frequency lower bound;
- high-value rare-event seeds remain discoverable but are marked as seeds, not
  approved taxonomy labels;
- bounded source examples support review without copying source corpora into
  the repository;
- every unit has a durable runtime checkpoint and can be resumed.

Generated checkpoints, SQLite products, CSV, status logs, manifests, and
Markdown audits are written below:

```text
D:\TradingML\runtimes\text_intelligence\candidate_inventory_v1
```

On the workstation, the same relative runtime path resolves below its local
`D:\TradingML\runtimes`. Set `QW_MLOPS_ROOT` or
`TEXT_CANDIDATE_INVENTORY_ROOT` only when the machine runtime authority differs.

## Preflight

```powershell
python -m research.text_intelligence.candidate_inventory_v1.run_build
```

The dry run checks source tables, certified News V2 authority, source row
counts, resolved units, and runtime location. It performs no corpus mining.

## Bounded validation

```powershell
python -m research.text_intelligence.candidate_inventory_v1.run_build `
  --source all `
  --workers 8 `
  --max-documents-per-source 1000 `
  --execute
```

A bounded run is always marked `partial`; it cannot certify corpus coverage.

## Full build

```powershell
python -m research.text_intelligence.candidate_inventory_v1.run_build `
  --source all `
  --workers 8 `
  --execute
```

The number of workers bounds concurrent ClickHouse readers and mining
accumulators. More workers are not automatically faster: SEC rendered documents
are wide and ClickHouse decompression can become the limiting resource.

## Products

- `candidate_inventory.sqlite`: review and downstream-query authority.
- `keyword_candidates.csv`: ranked single-token review projection.
- `phrase_candidates.csv`: ranked review projection.
- `AUDIT.md`: coverage, truncation, value, and top-candidate overview.
- `run_manifest.json`: exact version and configuration.
- `status.jsonl`: durable unit lifecycle and failures.
- `units/*.pickle.gz`: resumable bounded accumulators and stable cursors.

`estimated_document_frequency` is the Space-Saving estimate.
`document_frequency_lower_bound` subtracts the replacement error and is safe
for minimum-support filtering. Any document that reaches the explicit
per-document candidate bound makes the final run `partial`; the pipeline never
silently certifies truncated discovery.
