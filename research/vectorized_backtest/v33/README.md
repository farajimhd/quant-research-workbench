# V33: global evolution from V32 initial pools

V32 optimized global replay, objective, reproduction, speciation, risk and memory limits are retained. No local evolution is run. No evolved V32 descendants are imported.

CPU preparation reconstructs each original 6,144-candidate initial population with V32 seed 2020 and its saved per-session calibration probe. The complete population digest must match the sealed first-generation receipt before selection. Selection uses a separate deterministic RNG, balanced across 24 training sessions: 512 profitable (rank-weighted random), 256 active nonprofitable (rank-weighted by profit minus 0.25 drawdown), and 256 uniformly random remaining candidates. Category shortages fill randomly, and exact duplicate identities are refilled from the same session. All category counts and provenance are recorded. Sampling does not use validation.

The initial 1,024 candidates are evaluated across all 24 training sessions under the unchanged V21 global objective; validation remains display-only. Initial fitness, species, pedigree and RNG start fresh. Original candidate representation remains v32-recurrent-v1 for exact identity.

Prepare with `python -B -m research.vectorized_backtest.v33.prepare_initial --campaign <V32 campaign> --training <training.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v33/initial-pool`.

Launch with `python -B -m research.vectorized_backtest.v33.run_train --training <training.json> --validation <validation.json> --initial-pool <seed-pool.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v33/campaigns/global-initial-p1024-s2020 --commit <commit> --authorize-run`. Defaults: 50 global generations, 1,024 population, seed 2020, batch 512, two shared replay sessions, capture 1 GiB, bounded CPU prefetch, bars reserve 128 GiB plus 32 GiB load admission.

Set PYTHONDONTWRITEBYTECODE=1 for every invocation. Runtime artifacts belong only beneath D:/TradingML/runtimes. Global detailed panel: `python -B -m research.vectorized_backtest.v33.observe --output <campaign>/global`.
