# Convolutional encoder experiment

Hypothesis: shared temporal convolution filters preserve useful local bar patterns
with fewer encoder parameters than the medium MLP. Throughput and generalization
must be measured; neither is guaranteed by parameter count.

The versioned `conv1d_v1` backbone replaces only the encoder. All preparation,
training-only calibration, visible-only median calculation, random 16/64 masking,
losses and affine decoder remain unchanged. Scatter observed bars into their 64
chronological positions, including an explicit visibility channel. Retain M02's
16/visible-count density normalization. Broadcast the 18 reference input fields
across positions, producing 73 convolution channels. Six depthwise kernel-5,
stride-2 convolutions with pointwise mixing, GroupNorm and GELU reduce length
64 to 1. Width 128 uses channels 128,128,256,256,256,256. All encoder learned
projections are Conv1d; there is no attention or linear layer in this encoder.

An FP32 pointwise convolution emits 480 shape values. A learned FP32 pointwise
projection of the visible reference token emits 32 scale values. Together these
are the exported 512-dimensional embedding. `decode(z)` accepts only this vector:
the unchanged 512-to-3456 affine decoder predicts all bar fields and the unchanged
32-to-9 reference head predicts the medians. No reference, validity mask, time
metadata or input bar bypasses the embedding into the decoder. FP64 native-unit
inversion uses predicted references exclusively.

Compare against M02 using the frozen 67,606 training / 11,146 development subset,
seed 110731, batch 1024, 19,984 updates, cosine 3e-4 to 1e-5, no dropout, visible
loss weight 0.5 and input price scale 10,000. Profile additional batch sizes in
separate measurements without treating differing update/sample budgets as matched
scientific comparisons. Require tiny-overfit success, finite gradients, exact
mask/decoder contract tests, raw reconstruction and scale recovery evaluations.
Report masked and all-visible quality separately, worst groups and frozen probes.
Do not select on training loss alone. Use W&B `bargpt-phase1-v11-mlp512` with a new
run identity. Runtime records live under `phase1-v11-conv512-20261118`.

Wait for the MLP's complete epoch-2 validation before graceful handoff; preserve
its optimizer, RNG, traversal checkpoint and logs for a later resume. A finite
experiment is authorized; full-data continuation depends on measured quality.

## Measured results (2026-11-18)

Both candidates completed 19,984 updates and full frozen-development evaluation,
including frozen information probes, without consulting the audit subset. The
source snapshot is `dc6063ec3241019fa4279a7e18ddec12b6327611`. Twenty-two tests passed
on both laptop and workstation. Exact commands, hashes, all-channel raw metrics,
group regressions and pipeline profiles are retained in the runtime comparison.

| Candidate | Encoder parameters | Masked price error | All-visible price error | Masked activity log MAE | All-visible activity log MAE | Cached windows/s |
|---|---:|---:|---:|---:|---:|---:|
| M02 MLP control | 6,264,384 | 1.2087% | 1.1403% | 0.74547 | 0.54794 | 343,121 |
| C01 width 128 | 389,494 | 1.0922% | 1.1861% | 0.78830 | 0.74428 | 186,705 |
| C02 width 64 | 134,070 | 1.1306% | 1.1073% | 0.79366 | 0.75547 | 251,984 |

The unchanged decoder has 1,773,225 parameters. C02's complete model has 1,907,295.
Its full-stream batch-2048/16-worker profile reached 230,924 windows/s over 6.144M
windows, versus the historical MLP profile's 257,435 over 10.24M; these are bounded
prefixes, not sustained full-epoch throughput. Calibration, seed, samples, updates
and batch size match the scientific M02 comparison; random trajectories are not
identical because initialization differs and validation cadence is wall-clock based.

The final C02 profile at batch 8192 / 16 workers reached 255,530 windows/s over
8.192M windows (37.95s startup, 0.167s loader wait in 32.06s, 3.96 GiB reserved).
This approximately matches the MLP's best historical full-pipeline profile,
although C02 remains slower on the cached compute comparison. Batch 8192 is the
fastest measured convolution pipeline configuration; any scientific full-training
use would need its own explicit batch/optimizer contract.

C02 improves the within-window return probe R2 to 0.466 (MLP 0.169; untrained C02
-0.121), but activity, spread and volatility probes do not establish broad benefit.
Predicted median price-scale mean error is 0.160%, with no dead embedding dimensions.
Worst valid price-band mean error remains extreme: 611.52% versus MLP 607.83%.
All-visible daily trade-count raw MAE doubles versus MLP (6159 versus 3062).
No claim of forecasting improvement follows from these within-window probes.

Retain both as research checkpoints; C02 is the faster convolutional candidate,
but neither is an overall replacement for MLP. Do not launch a long convolutional
full-data run based solely on the improved average price metric. MLP was gracefully
paused only after complete epoch-2 validation; its checkpoint, optimizer, RNG and
traversal state remain preserved. Its full-data validation is not the finite-subset
MLP control shown above.

W&B runs in `bargpt-phase1-v11-mlp512`: C01 `985nuab9`, C02 `nsa65fr1`.
