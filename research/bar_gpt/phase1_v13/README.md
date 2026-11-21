# Phase1 V13: bounded attention stability comparison

V12 developed excessive Q/K magnitudes, saturated attention and numerically inaccurate BF16 attention gradients. This version tests fixed-norm queries/keys and FP32 attention arithmetic before changing capacity. It does not resume or overwrite the stopped V12 full run.

The encoder remains width128, two layers, four heads, exported256D. The unchanged lightweight decoder receives only that embedding and reconstructs64x16 frozen Phase0 latents. Each training window selects16 fresh random visible bars. The teacher, training-only calibration, source validity, loss, decoder and original split remain unchanged. Stabilized attention computes layer normalization, QKV projection, Q/K normalization, SDPA and its output projection in FP32; the stem and feedforward path retain the original autocast policy. Each Q/K head has fixed L2 norm sqrt(head dimension), bounding ideal attention scores by sqrt(head dimension). No trainable unbounded temperature is added.

Four matched arms compare original/stabilized attention, each with fresh initialization or the retained best V12 model and optimizer state at step74000. All use fixed LR3e-5, batch1024, seed123, identical sampled window indices and masks, and3000 updates. Best initialization loads the original Adam state; fresh arms initialize the same weights. Normalizing Q/K changes the warm-start function immediately, so step0 evaluation is mandatory. There is no claim that warm-start surgery preserves outputs.

The frozen training subset has67606 windows; development has11146. Teacher FP32 targets are cached in GPU memory for bounded iteration only. Reported cached training throughput includes sampling, model, backward and optimizer, excludes evaluation and initial cache preparation, and is not comparable to the full raw-data pipeline's161K windows/s. Each arm has a15-minute deadline, separate W&B identity, periodic evaluation, final checkpoint/hash and comparison receipt. An interrupted arm must not be reported complete. STOP under the new output directory is honored, and the old full-run STOP remains untouched.

Evaluation reports hidden-only latent loss and hidden-only1s trade-close MAE for16-visible reconstruction, and corresponding all-visible metrics. The same complete development subset and deterministic masks are used for every arm. These are reconstruction measurements, not forecasting results, and not full-population validation.

Run from a committed, pushed, hash-verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe' -B -u -m research.bar_gpt.phase1_v13.experiment --output 'D:/TradingML/runtimes/bar_gpt/phase1-v13-stability-20261121' --revision f7a69823036ee1c0c8616708360b93e602417db3
```

An existing output is rejected. Verify workstation GPU ownership before launching. The source expects preserved V12 teacher/calibration/checkpoints and the certified frozen subset at their existing runtime paths. All receipts and generated artifacts belong under the runtime output. W&B project: `mehdifaraji/bargpt-phase1-v13-attention-stability`.

## Executed results

All four3000-update arms completed, followed by four separately identified15000-update arms from the same starting states and seeds. The longer comparison is under `D:/TradingML/runtimes/bar_gpt/phase1-v13-stability-15k-20261121`; reproduce with `--steps 15000` and a new output directory. Each long arm took105–123seconds excluding shared cache preparation and W&B setup. Eight local tests passed. The original full run remains stopped, its STOP and checkpoints unchanged.

| Initialization / attention | Masked latent loss | Masked1s close MAE | All-visible1s close MAE | Cached windows/s |
|---|---:|---:|---:|---:|
| Best / original |0.067711|$0.4134|$0.3247|149766|
| Best / stabilized |0.067718|$0.1934|$2.2728|129443|
| Fresh / original |0.067955|$0.3144|$78.9861|151180|
| Fresh / stabilized |0.067945|$0.8181|$54.9759|128632|

Stabilization did not establish an overall winner. From the best checkpoint it improved masked1s close MAE by53%, but all-visible error increased approximately7x and cached training throughput fell14%. Fresh stabilization improved all-visible error relative to fresh original, but both remained unacceptable. This establishes a visibility-generalization issue on these subsets; the specific causal role of CLS/readout versus other architecture choices remains to be isolated. No full restart is recommended from these results alone. The LR-only best-checkpoint continuation is the strongest measured all-visible candidate, not a long-run stability certification.

The post-run audit reused V12's full source-valid reducers over all11146 development windows: all18 raw channels, six timeframes, directional/range statistics, variance and constant/context controls. All four had zero nonfinite predictions. Frozen context mean masked loss was0.081251. Detailed outputs and checkpoint hashes are retained per arm; W&B summaries use `detailed_development/` for the audited metrics. Initial experiment display reconstructed raw targets from FP32 logs; the audit uses actual source values and agreed within the declared tolerance. The current runner now retains original raw targets directly. This display correction changes neither the latent training objective nor the completed models.

All eight arms used the same fixed training-only per-dimension z-score transform for the16 Phase0 embeddings. Its scales span0.195660–0.663896, with no constant dimension. No unnormalized, min–max or robust-scaling arm was run. Such a comparison must separate input conditioning from loss reweighting; per-example normalization must not discard recoverable scale, and train-only min–max must not silently clip future out-of-range values.
