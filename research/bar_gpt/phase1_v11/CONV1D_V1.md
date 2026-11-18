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
