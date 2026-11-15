# Phase-two V2 coordinate-token masked predictive encoder

Approved2026-11-15. New architecture/run, not a continuation of V1 weights or optimizer.
V1 is copied to this independent version; historical data and teacher contracts are unchanged.

## Scientific contract
Frozen SHA-pinned completed phase-one V4 epoch50 encoder AND its same-checkpoint decoder.
16 contexts:8x64 1s,4x64 10s,2x64 1m,1x64 1h,1x64 daily.
Tokenizer emits Bx16x512. Draw a fresh batch-shared random subset of192 coordinate IDs
of512 each optimizer update. Gather before encoder; transpose to Bx192x16 and add
learned original-coordinate identity16. No timeframe/metadata embeddings or input summary.
Stem pre-LN16, QKV16->192,4heads16, concatenate64; residual Linear16->64.
Six pre-LN64 SDPA blocks,4heads16,FF256,GELU,zero dropout. Final LN64.
Shared per-token Linear64->512 GELU, then mean visible tokens ->B512 output summary.
Light decoder512->256 GELU ->512. Future target is frozen V4 embedding of next64
observed1s bars. MSE ONLY the320 dropped coordinate IDs; target detached.
Eval/production uses ALL512 input tokens; full-coordinate MSE/cosine/variance,
persistence/training-mean controls and target-conditioned raw-unit decoded diagnostics.
Frozen decoder and target refs/clocks are validation-only, never optimization inputs.

## Data and traversal
No shard changes. Same certified all-published4TB release; chronological unit/block/origin
traversal, stride1 observed1s origins. Completed coarse end<=origin; all required history
must exist, next64 observations must remain inside source block. No padding/truncation.
Unique endpoint banks reused only within exact receipt/teacher/precision/split basis.
V2 ignores the loader's9 metadata values; they are retained as evidence for compatibility.
No future target or reference enters the encoder.16 context slots have fixed frame/order.

## Training and recovery
TEN full dataset passes, not ten validations. Initial peak3e-4/floor5e-5,once-only2000
update warmup. LR updates only at full validation and pass end. New full-pass peak/floor
multiplied0.9. AdamW weight_decay.01,clip1,patience8,finite guards and human STOP retained.
New W&B run in bargpt-phase2,run name from new output directory. Exact contract-bound
resume preserves model/AdamW/scheduler/RNG/cursor/history. No V1 weight compatibility bypass.
Rolling last/validation/best checkpoints; immutable checkpoints only at complete pass ends.

Use python -B -m research.bar_gpt.phase2_v2.run_train with explicit --dataset-release,
--output D:/TradingML/runtimes/bar_gpt/<new-run>,--mode train --execute --device cuda
--precision bf16 --wandb-mode online --source-revision <committed immutable SHA>.
Run with PYTHONDONTWRITEBYTECODE=1. Default architecture is fixed to approved values.
Execution batch/cache/workers/validation interval are qualified on native hardware before
launch. Qualification artifacts belong in native runtime, never source.

## Native qualification2026-11-15
Six focused tests passed on laptop and native CPU, including real shard causal/index
parity and exact interrupted model/AdamW/scheduler/RNG/cursor recovery. Native CUDA
capture matched ordinary four-update model/optimizer/RNG state exactly. Batch sweep
512/1024/2048/4096 selected1024: cached compute34863 origins/s with capture versus32995
without (5.66percent), not whole-corpus throughput. First40 training-publication pipeline
trials were about20k origins/s including preparation;8 prep workers gave no repeatable
improvement over4. Keep4prep/4IO. Real128-example/four-training-block masked overfit
reached full-coordinate MSE.004096 versus.325151 initial and.024628 mean baseline after
6000updates; these disposable weights do NOT initialize full training. Qualification-only
LR1e-3 then3e-4; full training retains approved3e-4 peak. GPU integration completed an
actual small dataset pass,2945-origin decoded validation and checkpoint reload with no
teacher gradients. No guarantee of generalization or corpus speed from these probes.

## End-to-end execution and profile2026-11-15

Current qualified execution additionally supports `--student-attention-backend cudnn`
with CUDA replay. Architecture, parameter shapes, fixed teacher, batch1024, masking,
loss, data selection, chronology and schedule are unchanged. This changes the SDPA
implementation, not attention semantics. It is numerically equivalent BF16, NOT
bitwise replay:64same-input updates had relative loss delta.000127673 and maximum
weight delta.000538; independent FP32 math-attention reference checks passed all7layers.
A stricter experimental absolute-output test failed and remains in runtime evidence.
Do not reinterpret that raw failed report as exact parity.

The original full-pipeline64-update CUDA trace showed2035preparation-thread stream
synchronizations plus64main finite guards. Frozen-tokenizer streams used about43percent
of summed kernel time; main student/metrics about57percent. These are overlapping
streams, not additive wall percentages. Original per1024 GPU kernel totals:~23ms frozen
preparation and~30.6ms student/metrics, of which~3.2ms metrics. Student input192x16 is
widened to192x64, and nonlinear readout materializes192x512 before pooling. Smaller
parameter count therefore does not imply less activation/reduction work thanV1.

Execution order:
1. Four CPU I/O workers verify immutable receipts/hashes/arrays.16-publication warm-cache
   sample:3.77ms CPU work per1024eligible origins; causal planning.436ms per1024.
2. Four publication-preparation workers own separate CUDA streams. Pool arrays now
   use one pinned host allocation/copy and asynchronous H2D; no additional numpy-copy
   plus pinned-copy. CPU clock eligibility uses the exact immutable source clocks,
   eliminating repeated GPU-to-CPU domain-check readbacks. Lookup values still come
   from the validated shared UTC transform tables; out-of-domain fallback unchanged.
3. Frozen V4epoch50 remains eval/requires_gradFalse, under no_grad. Same Float64 local
   references, BF16 encoder and fixed2048 endpoint tiles. All original predicates are
   accumulated and checked BEFORE publication readiness. No unchecked session can
   reach an optimizer update. Direct normalize/ensure calls retain immediate checks.
4. One next-batch preparation future gathers cached endpoints and packs consecutive
   eligible origins. CUDA event waits and allocator stream records protect ownership.
5. Training draws fresh192IDs, replays student forward/maskedloss/backward/clip, checks
   finite loss+norm BEFORE fused AdamW, advances exact source cursor and scheduler.
6. Identical Float64 metric arithmetic is captured in the graph; count changes only
   after accepted optimizer updates. Reset zeros the same buffers across validations.
   Capture warmup restores metrics/RNG, and partial batches retain eager metric logic.
7. Logs every10updates,rolling recovery every1000,full validation/LR update150000 and
   complete-pass boundaries. Full validation decoder/reference conditioning unchanged.

Interleaved262144-origin trials at one fixed parent/cursor: original18.93-19.01k/s;
coalesced checks/captured metrics20.97-21.16k/s;plus cuDNN22.34-22.43k/s. Producer wait
fell2.15-2.26s to.40s over256updates. A second fixed-parent comparison of asynchronous
pool/CPU-clock checks gave21.19-21.28k/s before and21.94-22.09k/s after. Do not multiply
or extrapolate these workload-specific gains. A first resumed180s live window sustained
22.24k/s versus20.02k/s pre-change; latest final live result is in the operational setup.

Cache scope is explicit: a16-publication schedule audit found zero duplicate endpoint
encodes WITHIN each publication. It encoded124629fixed-tile windows for76720requested
unique endpoints. Extra tile windows preserve existing frozen-target numerical geometry;
not all are necessarily later consumed. Cross-publication redundancy is not globally
eliminated. Do not silently reduce tiles/change teacher batch shapes or claim zero waste.

Qualified source transitions use engineering_resume.py: only implementation hash and
explicit student backend may change; model/AdamW/scheduler/RNG/cursor/history/metrics
must be preserved. An independent checkpoint and same-ID/name W&B signature bridge
are sealed, originals retained. Batch, dataset, teacher, losses and schedule changes fail
closed. Reporting's ordinary signature and lease checks remain intact.

Ten focused tests pass on both machines, including negative source predicates, clock
boundaries, exact source/target parity and full recovery. Actual GPU integration completed
two small full passes/two decoded validations and checkpoint reloads,2945validation origins.
Generated profiles,parents and ledgers are under native runtime, never the repository.

## Major production bottleneck: eligibility journal (subsequent correction)

The earlier benchmark loop omitted the production eligibility-report callback.
It therefore did NOT reproduce the whole production input path. Production stage
telemetry measured about51.5ms/update waiting for input versus27.2ms compute/finite
guard,.36ms optimizer/metric-count dispatch and.08ms reporting in one100-update window.
A3-minute live window fell to18.83k/s despite faster benchmark loops; this regression
was retained and investigated rather than called a deployment success.

The concrete missing cost was reopening/closing the growing eligibility.jsonl for
EVERY source-block record on the next-batch prefetch thread. A5.5MB copy measured
156.7-159.5ms/append with reopen versus.010-.015ms with a held line-buffered handle.
The exact Windows filter/component causing the open/share overhead was not isolated;
do not attribute it to antivirus without further evidence. It was not W&B latency.

Full production-equivalent comparison INCLUDED identical callback records,original
journal size,original source cursor/model/AdamW/RNG and131072origins per trial:
reopen11.576-11.674seconds /11228-11322origins/s versuspersistent5.938-5.946seconds /
22044-22075origins/s. Both wrote54records and ended at the SAME cursor. Journal wall
time fell8.96-8.99seconds to.007-.010seconds. Input wait fell8.13-8.15seconds to.38seconds.
This~1.95x result is on matched work,not a corpus-wide speed promise.

Full-pass training now opens one UTF8,line-buffered append handle. Every newline
flushes immediately,so records are not omitted or deferred until shutdown. Source
workers drain before the handle closes. Tests verify exactly one open per training
invocation and closure on normal completion,interruption and resume,with exact
checkpoint/RNG/cursor recovery. Source/data/model/loss/scheduler contracts unchanged.

Additional checks ruled out debug CUDA environment overrides and a one-core affinity
mask. A live disk sample showedD:~.3ms read latency andempty queue. Frozen-tokenizer
forward graph replay on existing2048 and1..16tile shapes was byte-exact on samples but
~.7percent slower in controlled full-pipeline trials; it was REJECTED and is not active.
No extra teacher graphs or graph workers are deployed. Code retains eval/no_grad,
asynchronous pinned copies,four publication streams and one next-batch future.

Current authoritative native setup after this fix:
D:/TradingML/runtimes/bar_gpt/phase2-v2-journal-fix-20261115/OPERATIONS.md.
See its live-check.json for final sustained actual-training timing and progress.
