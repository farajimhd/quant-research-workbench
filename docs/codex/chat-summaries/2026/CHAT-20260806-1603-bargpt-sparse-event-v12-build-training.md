# Rebuild BarGPT around a sparse-event v12 authority and align training, profiling, and evaluation

- Chat started: 2026-08-06 16:03:09 PDT (America/Vancouver)
- Chat ended or last activity: 2026-08-11 10:04:52 PDT (America/Vancouver)
- Summary written: 2026-08-11 10:04:52 PDT (America/Vancouver)
- Chat/task identifier: `019fd950-f414-78d1-8c38-6bbc255d938a`
- Repository or scope: `quant-research-workbench`, BarGPT v1 data authority, shard builder, loader, model, training, profiling, auditing, and model discovery
- Related task-history entries: `TASK-0170`
- Source completeness: Partial. The active chat, repository state, commits, runtime evidence, and task ledger were accessible, but some earliest assistant responses had been compacted. No inaccessible chat was reconstructed from assumptions.

### Narrative

The chat began as a readiness review of BarGPT training, profiling, shard loading, and a validation build. The user wanted to know whether the existing SSD-shard workflow was ready to keep the GPU occupied, whether the partially built 2026 data could support validation, and whether training or profiling could run beside the workstation build. Validation was standardized as one deterministic held-out panel, initially two blocks per ticker. Early work repaired loader metadata, ticker identity handling, validation transitions, and the circular import that prevented offline-shard construction. It also established the operational requirement that condition-derived metadata be produced by the normal builder rather than by a sequence of manual repair commands.

The focus then moved to model semantics and throughput. The model uses separately encoded one-second, intraday aggregate, and calendar views, with causal attention within views and gated fusion into the origin representation. The user corrected an erroneous assumption that every intraday view should contain 720 bars. The accepted configured counts became 720/360/360/240/240/96/16/8 for 1s/5s/10s/30s/1m/5m/30m/1h and 90/52/24 for daily/weekly/monthly views. These are configuration parameters; source lookback and warm-up must be derived from them and bar durations, never from a hard-coded 28,800-second constant. Missing history at the beginning of authority is represented by fixed zero-filled slots whose masks are false, while real history gradually replaces those slots.

Auditing the original shards changed the project more fundamentally. Manual notebook inspection and automated reconstruction showed that dense clock positions with no market event had been admitted as origins and context. This wasted computation and made the model learn a representation dominated by empty seconds. The user rejected an in-place shard repair because removing empty positions changes context, causal indices, aggregation, and targets. The data contract was therefore redesigned around sparse eligible events and a complete rebuild.

The final sparse-event rule is stricter than merely dropping empty origins. A one-second bar exists for this model only when it contains an eligible trade; seconds without an eligible trade are neither origins nor context tokens. Coarser intraday bars are formed vectorially from eligible sparse source bars, and empty aggregate buckets are omitted rather than inserted as zero bars. Each view still has a fixed tensor length: it contains the most recent configured number of nonempty completed bars, left-padded with masked zeros only when the historical authority truly does not yet contain enough eligible bars. Every stored bar carries real start, end, and availability timestamps, and as-of indices are causal and independent of shard or month boundaries.

Target design also evolved after early model-discovery runs showed direction metrics near chance and the user questioned whether the model was learning the intended task. The target authority now produces trade, bid, and ask open/high/low/close returns for each physical horizon and for autoregressive objectives. Open is the first eligible family update in the future interval; high and low are extrema; close is the last eligible update at or before the horizon. No midpoint target is used. Each return field has its own direction head, direction loss is backpropagated, and the existing unbalanced loss is retained without class weighting or a balanced-loss substitution. The neutral-band behavior remains explicit. Arbitrary return clamping, including the earlier 2,000-basis-point cap, was rejected. A skill-versus-zero overfit gate was also removed because tiny baseline returns made that ratio misleading even when absolute MAE was small; direct loss, MAE, direction, calibration, and distribution diagnostics remain the useful evidence.

Condition handling required several rounds of clarification. Trade and quote condition namespaces cannot be interpreted from the numeric code alone, and historical compact events do not preserve every field needed to reconstruct all original correction semantics. The user ultimately decided not to rebuild the immutable `events_YYYY` authority or implement a speculative historical correction overlay. The unresolved correction-01/12 limitation is documented rather than silently “fixed”; future event ingestion should retain sufficient correction lineage. For the present sparse contract, condition inputs and targets are preserved as event counts/presence over eligible sparse bars and aggregates, while ineligible price events cannot create origins or price context. Constant or redundant inputs such as `trade_present`, which is implied by origin eligibility, were removed. Point-in-time split adjustment remains required for both context and targets.

Because materializing and rereading intermediate one-second and daily tables was slow and consumed SSD space, the builder was redesigned to read the compact ClickHouse event authority, perform restricted sparse aggregation in bounded vectorized batches, send arrays to Python, compile final tensors, and persist only final shards. Pilot runs exposed slow warm-up, misleading two-stage progress, buffered worker updates, partial-month audit failures, and an incorrect dense-context assumption. Those were repaired by deriving sparse lookback from configured nonempty counts, reconstructing partial shards correctly, and reporting non-resetting source, compile, and certification stages. The old BarGPT one-second ClickHouse tables were later dropped after verification that the direct-event path no longer depends on them.

Pilot shards and the overfit path were used as gates before the full build. The audit checks hashes, schema and feature dimensions, masks, causal indices, true timestamp ordering, sparse-origin eligibility, OHLC geometry, AR and physical-horizon targets, block coverage, and sampled ClickHouse-to-tensor reconstruction. The notebook was updated to load the current contract. Overfit initially failed because pilot coverage was incorrectly subjected to full-catalog discovery, then because older gates overemphasized direction and skill-versus-zero. After contract and evaluation repairs, the compact model learned the pilot much more strongly, providing evidence that the revised heads and targets can backpropagate. This is a learning-path smoke test, not proof of held-out generalization.

The production dataset was frozen as one immutable catalog rather than separate training and validation builds. It samples 300 tickers deterministically, spans 2019-01-01 through 2026-08-01, and is built in one pass. Training will select 2019-2025 data and validation will select 2026 data from the same locked authority. Future data or expanded cohorts must use a new output folder. The cohort hash is `069d7b781ffe6d7dfa4d4168f7fde7791cf79d9a115418cb77820e2eae07651d`. The v12 build plan contains 27,300 ticker-month units, uses 32 workers, requires approximately 5.5 TB of free capacity in preflight, and runs automatic audit and catalog-lock stages after compilation. More Python workers do not increase the configured 32 concurrent ClickHouse page reads, so 32 remains the supported starting point.

The live workstation build was inspected while it ran. Its plan requested storage contract 12, loader stream contract 13, the intended date range and 300-ticker cohort, direct-event mode, and an immutable v12 output. Two complete early shards, `UVXY:2019-04` and `XLE:2019-04`, passed a bounded structural audit: SHA-256, 50-feature schema, eleven views, trade-only origins, masks, causal indices, OHLC geometry, targets, and block coverage all passed. They contained 42 blocks/134,976 origins and 46 blocks/168,188 origins respectively. Both had zero positive condition counts, so they did not prove positive halt/resume/news-risk/LULD behavior. Sampled source reconstruction was intentionally deferred during the active build to avoid ClickHouse contention; the builder's post-build audit remains the authority for that gate. At the last verified observation the build had certified 417 shards with zero reported failures and was still progressing. A later status refresh from the laptop was denied by workstation share permissions, so completion must not be inferred.

The loader, trainer, profiler, inference, discovery, and audit paths were then reviewed against v12. Storage geometry and contracts now come from the immutable build manifest rather than hard-coded consumer values. Historical view masks are passed consistently, valid-origin counts remain CPU metadata to avoid a per-microbatch GPU synchronization, and production loading uses pinned batches, a CUDA prefetch stream, bounded queues, and deterministic length bucketing to reduce padding while preserving exact resume state. Validation workers and prefetched batches are retained across validation; checkpoints are written asynchronously after validation. Resume state includes model, optimizer, scheduler, scaler, update counts, durable data cursors, and deterministic panel identity.

The prior “72 checks” profiler was diagnosed as a 4-by-3-by-3-by-2 loader-only Cartesian grid. It did not run model forward/backward, so a Task Manager reading near 76 percent could not establish model GPU utilization. The default benchmark is now a bounded 12-candidate loader grid over useful worker, prefetch, and host-cache values. Model profilers were aligned to the production loader shape: ready queue 128, prefetch depth 2, and length bucket 4. Routine comparison now runs current, medium, and large models only. The time-consuming xlarge model was removed from the default comparison and the discovery grid, while remaining available only through an explicit custom preset. Discovery now has seven architectures, versioned v8 manifest/state files, fail-closed state/manifest matching, and a v3 final-validation namespace so stale results cannot be reused. Final validation resolves exact certified panel references rather than demanding unrelated full-range coverage.

The final tooling alignment changed twelve BarGPT files. Ten focused tests passed during development; the complete BarGPT v1 discovery run then passed 195 tests in 16.153 seconds. CLI smoke checks confirmed the three-model comparison, seven-architecture discovery plan, production-shaped no-W&B profiler configuration, loader benchmark, final validation, and overfit entry points. Task-owned diff checks passed; an unrelated trailing-whitespace defect in a user-owned market-SIP file was left untouched. Commit `6138408c` (`fix: align BarGPT v12 evaluation tooling`) was pushed to `origin/main`. Differing workstation files were backed up under `D:\TradingML\runtimes\bar_gpt\v1\code_sync_backups\6138408c-20260811-1000`, the twelve files were synchronized, and their SHA-256 hashes were verified. The active builder continued after synchronization.

Separately, ClickHouse memory pressure was investigated before the full build. No stale query, mutation, or merge was active. Most apparent memory was reclaimable WSL filesystem cache, but the SEC Gateway was repeatedly running an expensive full `FINAL` XBRL reconciliation. The user paused that gateway, reducing interference with the shard build; it should remain paused during the expensive build and be resumed afterward, with a future task addressing incremental SEC reconciliation.

### Durable decisions

#### Confirmed requirements

- Only eligible-trade seconds may create BarGPT origins or context bars. Empty seconds and empty aggregate buckets are omitted.
- Context tensors have configured fixed lengths of 720/360/360/240/240/96/16/8 intraday bars and 90/52/24 calendar bars. Missing authority history is masked padding, not a fabricated market bar.
- Source lookback is derived from configuration and sparse availability; it is not a hard-coded number of one-second clock rows.
- Targets include trade/bid/ask OHLC returns for every physical and AR horizon, with a separate backpropagated direction head for every return field. No midpoint targets, balanced loss, or arbitrary return clamp is allowed.
- Timestamps, availability, masks, causal indices, split adjustments, condition evidence, and deterministic resume state are contract data, not optional diagnostics.
- The 300-ticker 2019-through-July-2026 v12 catalog is immutable. Validation is selected from 2026 within it. New data must be written to a new versioned folder.

#### Architectural decisions

- Build final shards directly from compact ClickHouse events; do not persist intermediate BarGPT one-second or daily authorities.
- Use fixed recent nonempty bars for each view and mask only unavailable historical slots.
- Load storage contracts from the build manifest across all consumers.
- Keep queues bounded, preserve validation workers, bucket blocks by length, prefetch to CUDA, and checkpoint asynchronously after validation.
- Keep routine comparisons to current, medium, and large; make xlarge explicit-only.

#### Rejected approaches

- Patching dense shards in place, using empty clock bars, rebuilding separate train/validation datasets, and fixing context to 28,800 clock seconds were rejected.
- Rebuilding immutable historical event tables or inventing a correction overlay without sufficient lineage was rejected.
- Skill-versus-zero as a required overfit gate, balanced directional loss, midpoint targets, and 2,000-bps clipping were rejected.
- Treating the 72-case loader sweep or Windows Task Manager utilization as a model throughput benchmark was rejected.

#### Assumptions

- Compact events remain the historical source authority despite the explicitly documented correction-lineage limitation.
- Thirty-two workers are an appropriate full-build default; higher process counts need measurement and do not bypass the 32-page ClickHouse concurrency cap.
- The two sampled shards are representative for structural format, but not for positive condition behavior or full-catalog source parity.

#### Unresolved uncertainty

- Positive halt/resume/news-risk/LULD examples still need to pass the completed-build condition audit.
- Full ClickHouse reconstruction, catalog completeness, exact 27,300-unit certification, and lock status are unknown until the live build finishes.
- Optimal loader concurrency and model size/quality trade-offs remain empirical questions for the completed v12 authority.

### Delivered outcomes

- Implemented and documented the sparse-event v12 builder, loader/model contract, audit/notebook, pilot, overfit, profiling, comparison, discovery, and final-validation alignment under `research/bar_gpt/v1`.
- Froze the 300-ticker one-pass catalog plan and launched the active workstation v12 build.
- Dropped obsolete BarGPT one-second ClickHouse tables after the direct-event authority was established.
- Passed a bounded structural audit on two completed v12 shards and recorded the limitation of their zero-positive condition counts.
- Reduced the default loader sweep from 72 to 12 candidates, aligned profiler loader geometry, removed xlarge from routine comparison/discovery, and fail-closed discovery state reuse.
- Passed all 195 BarGPT v1 tests and relevant CLI smokes.
- Pushed commit `6138408c` and hash-verified the synchronized workstation files after creating a runtime backup.
- Diagnosed the SEC Gateway's expensive full reconciliation and paused it to protect the build.

### Unfinished or hanging work

- **Complete and lock v12.** Current state: live build in progress; last verified at 417 certified shards and zero failures. Why unfinished: the laptop could not later refresh the protected workstation share and the post-build lifecycle had not completed. Next action: let the launcher finish and inspect its terminal, manifest, failures, audit artifacts, exact unit count, and immutable lock. Dependency/owner: workstation build/user. Related task: `TASK-0170`.
- **Run final data gates.** Current state: two structural samples passed; source reconstruction and positive-condition coverage are not certified. Next action: run/inspect the automatic structural, source, sampled ClickHouse reconstruction, dataset-summary, and condition-positive audits after the builder releases ClickHouse. Dependency: complete v12 build. Related task: `TASK-0170`.
- **Benchmark the production loader and model.** Current state: code is aligned, but the new 12-candidate loader benchmark and model profiler have not run against the locked catalog. Next action: benchmark loader starvation/H2D/padding and then run the no-W&B model profiler; select settings from end-to-end throughput, not Task Manager alone. Dependency: locked v12 catalog. Related task: `TASK-0170`.
- **Re-run overfit and model comparisons.** Current state: entry points are aligned and tested, but no v12 post-lock learning results exist. Next action: overfit a certified bounded panel, then run current/medium/large comparisons and exact final validation. Dependency: audit pass and profiler-selected loader shape. Related task: `TASK-0170`.
- **Resume and repair SEC Gateway separately.** Current state: intentionally paused. Next action: resume after the shard build, then redesign the full `FINAL` reconciliation as bounded/incremental work in its own task. Dependency: build completion and user operational control. Related task: not added to `TASK-0170`.
- **Historical correction lineage.** Current state: documented limitation, not implemented. Next action: ensure future ingestion preserves sufficient correction identifiers and only revisit historical data if a deterministic authority becomes available. Dependency: market-SIP ingestion design. Related task: `TASK-0170` only insofar as the limitation affects its source authority.

### Handoff to the next chat

Read `TASK-0170`, this summary, `research/bar_gpt/v1/SPARSE_EVENT_CONTRACT.md`, the v12 build manifest, and commit `6138408c` first. Do not revert to dense one-second clock bars, hard-code source warm-up, create a separate validation dataset, reuse old discovery state, or reintroduce xlarge into routine grids. The immediate priority is not another code redesign: verify that the live 27,300-unit build completes, its automatic audits pass—including real positive-condition and ClickHouse reconstruction checks—and the catalog is locked. Only then benchmark the bounded loader/model paths and run overfit plus current/medium/large comparisons. Workstation access and SEC Gateway restart are operational actions; preserve the user's control over them.
