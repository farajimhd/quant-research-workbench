# Pipeline instructions

- Treat each bounded unit as acquire/extract, process, durably insert, checkpoint, then clean up temporary files.
- Pipelines must be idempotent, restart-safe, gap-aware, and explicit about skipped, retried, failed, and deferred units.
- Historical downloads and backfills must preserve raw/source authority, provenance, availability timestamps, timezone semantics, and canonical output contracts.
- Do not silently drop or overwrite source data. Prefer versioned tables and auditable cutovers for rebuilds.
- Use bounded concurrency and accurate progress totals. Keep generated data, manifests, logs, and caches under the machine-specific runtime root.
- Validate representative source-to-canonical rows, keys, ordering, partitions, deduplication, and final query semantics before declaring completion.
