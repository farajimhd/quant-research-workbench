# Phase-two unmasked V1

User-selected2026-11-15 after bounded same-data comparisons with stopped V2.
This is a fresh experiment, not a same-ID resume or an initialization from pilot
or collapsed student weights. W&B remains online in `mehdifaraji/bargpt-phase2`,
with a new run name/ID derived from the independent output directory.

Input:16 complete512-dimensional embeddings from frozen phase-one V4 epoch50.
Context order and history remain8x64 1s,4x64 10s,2x64 1m,1x64 1h,1x64 daily.
One pre-LN attention block: width512,8 heads of64,FF2048,GELU,dropout0.
Readout is existing latest1s row7, then Linear512-to256/GELU/Linear256-to512.
There are no added tokens, latent centering, feature masks or masking RNG.
The target is the frozen embedding of the next64 observed1s bars; loss is MSE
over all512 future coordinates. Original target references/clocks are used only
for the existing decoded validation diagnostics, never student inputs.

The certified4TB release, causal block eligibility, source ordering, tokenizer
tile2048/eval/no-grad policy, online reporting and full-state recovery are shared
with phase2_v2. Original V2 remains stopped under its human STOP. The source
manifest seals both the shared implementation and these version-specific files.

Retained native engineering configuration: BF16,batch1024,one CPU Torch thread,
four publication preparation and four I/O workers,8192MiB host prefetch,1024MiB
per-publication device cache,one next-GPU-batch prefetch,fused AdamW,cuDNN SDPA
and CUDA forward/backward/clip replay. The unmasked capture directly uses the
complete input without coordinate gathering. Nonfinite loss/gradient guards
still precede every optimizer update.

Schedule:10 complete passes over all eligible training data,not10 validations.
Validation and LR-update interval150000 optimizer updates plus pass end;
peak3e-4,floor5e-5,one-time2000-update warmup,and peak/floor multiplied by0.9
only at each new complete pass. Patience8,weight decay0.01,gradient clip1,
loss logging10 updates,rolling resumable checkpoint1000 updates,rolling
validation/best checkpoints,and immutable complete-pass checkpoints are retained.

Native launch uses `run_train.py --mode train --execute --dataset-release
<certificate.json> --output <new runtime directory> --device cuda --precision
bf16 --batch-size1024` with the retained explicit resource/schedule arguments.
`qualify.py` compares same-input eager versus captured student updates at the
same batch/backend/precision. It is not a corpus-speed measurement; verify
the actual running pipeline and remote W&B identity after launch.
