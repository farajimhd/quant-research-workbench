# Pipeline instructions

- Treat each bounded unit as acquire/extract, process, durably insert, checkpoint, then clean up temporary files.
- Pipelines must be idempotent, restart-safe, gap-aware, and explicit about skipped, retried, failed, and deferred units.
- Historical downloads and backfills must preserve raw/source authority, provenance, availability timestamps, timezone semantics, and canonical output contracts.
- Raw SIP flatfile access belongs only to the canonical download/import updater and only during acquisition, validation, and import of a new source day. Once certified, every downstream pipeline and repair reads `market_sip_compact.events_YYYY` (and ingestion-owned certified sidecars) exclusively; it must fail closed rather than reopen flatfiles or call ClickHouse `file()`.
- Do not silently drop or overwrite source data. Prefer versioned tables and auditable cutovers for rebuilds.
- Use bounded concurrency and accurate progress totals. Keep generated data, manifests, logs, and caches under the machine-specific runtime root.
- Validate representative source-to-canonical rows, keys, ordering, partitions, deduplication, and final query semantics before declaring completion.
- For agentic stages, require the requested final structured result, validate its contract, and retain enough reject status for diagnosis without logging sensitive source data. Do not silently replace an agreed agentic stage with deterministic heuristics.
- Use deterministic code for source preservation, parsing, identity, deduplication, hashes, timestamps, and integrity invariants; use model judgment only where semantic judgment is intended.
