# V7 full-data training

The selected geometry_joint encoder keeps width 2048, three residual blocks,
768 exported values, and one 768-to-1152 affine decoder. Training is all-visible,
with the selected equal-family log-coordinate objective. The user selected 50
epochs. A fresh seed-17 model is used; subset training is not resumed as full-data
training. All certified windows and worker tails are retained.

`full.py` implements precision qualification, compute profiling, real certified
stream profiling, and full training. `full_data.py` performs deterministic source
validation and feature construction in 256-row CPU chunks using the model's
shared preparation function. BF16, if qualified, affects only the dense backbone;
explicit scale slots, exported projection, decoder, and objective stay FP32.
Native inversion uses FP64. No fitted population calibration is required.

Source snapshots must include `research/__init__.py`, `research/bar_gpt`,
`research/mlops`, `pipelines/market_sip`, and `pipelines/certified_research`: the existing checkpoint integrity
utilities transitively import canonical pipeline modules. Importing these modules
does not read events or run a writer. Training reads certified shards only.
Commit and push before creating each immutable snapshot; pass its file-hash
deployment receipt to every invocation. Generated output belongs under runtime.

Full training retains rolling last/best checkpoints with optimizer, RNG,
epoch/batch traversal cursor, and scheduler position. Resume requires the same
contract and W&B identity. The ordered worker prefix is replayed without optimizer
updates. Human STOP files are never removed. Source geometry violations are fatal,
not filtered or clipped. Per-epoch validation must cover the full frozen partition.

Runtime profiling evidence and the final launch configuration are recorded under
`D:\TradingML\runtimes\bar_gpt\phase1-v7-full-setup-20261117` on the workstation.
