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
