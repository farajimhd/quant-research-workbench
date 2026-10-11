# Unified launcher and recovery

[Overview](../certified_research_dataset_design.md) · [Previous](09a-context.md) · [Next](10-storage.md)

Follow updater integration, resource limits, progress and safe restart.

## On this page

- [One launcher and restart model](#one-launcher-and-restart-model)

## One launcher and restart model

### Proposed launcher interface

Proposed single entry point exposes `plan`, `run`, `resume`, `status`, `verify`. This is an interface proposal, not an existing runnable command. Resolve configuration once and freeze a content-addressed plan.

### Integration with the updater

The controller wraps the existing updater rather than implementing a second downloader/encoder. It maps the frozen source range and D cache/mount roots, resource limits, explicit `sip_raw_ssd` storage policy and `--skip-bars` to that stage. Modes distinguish reuse-only, acquire-missing and explicitly planned canonical repair. Automatic remote discovery may propose new dates, but execution freezes fully published source days first; an updating provider frontier must not change a running dataset. Completion resumes from verified source-day receipts, not a downloader exit code.

### Configuration groups

Configuration groups: target/context ranges; use-case presets and metric catalog; session checkpoints; source/universe/public-availability policy; G archive and D cache roots/hydration mode; machine/runtime root; global resource budgets; disk headroom; retry bounds; split roles and consumer export options. Strict unknown exclusion is fixed in this dataset revision, not a convenient opt-out. Defaults are complete and safe; ambiguity blocks planning.

### Hydration and raw-file access

Hydration inventory includes context migration files. Existing reporting tool supports verified temporary copy, hashes, atomic publication and mismatch rejection. Canonical SIP flatfiles may be opened only by the ingestion-owned migration/import path. If all files cannot stage within D headroom, fail or explicitly plan bounded staging tranches. Never overwrite mismatched files or delete caches/old products without authorization.

### Storage policies and runtime roots

Canonical/operational data uses designated `sip_raw_ssd` / `live_market_ssd` policies; `default`/`hdd` backup routing is forbidden. Verify table policy and actual parts before writers, after writes and at certification. Runtime arrays, receipts, logs and caches stay under machine-specific `D:/TradingML/runtimes`.

### Progress, interruption and idempotency

Progress is dependency-aware: queued/active/reused/certified/empty/unavailable/blocked/retried/failed, counts, current unit, throughput and measured ETA. Redirected logs use plain summaries. Ctrl+C cancels owned read queries, drains workers, records uncertain writes/mutations and retains restart receipts. A server mutation may outlive its initiating process: resume reconciles it before continuing. Ownership locks prevent overlapping writers; source changes invalidate descendants through the state chain. Idempotency uses attempt IDs and verified publication, not blind repeated INSERT.

[Overview](../certified_research_dataset_design.md) · [Previous](09a-context.md) · [Next](10-storage.md)
