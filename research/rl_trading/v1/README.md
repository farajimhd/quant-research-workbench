# RL trading V1

This package builds a three-phase hindsight dataset for long-only portfolio teacher trajectories. Phase 1 reads certified completed 100 ms price bars and 1 s MACD/activity from `arte`, validates their build certificates, and labels price-action opportunities. It reads the pinned pre-open population from `q_live` for listing identity. Phase 2 compiles the full market-wide holding values and sparse liquidity-gated opening values. Phase 3 searches cash-constrained long-only trajectories using the Phase 2 tensor.

ClickHouse access is read-only. Market observations use certified `arte.bars_v1`, `arte.indicators_v1`, and the three certified causal V7 `arte` products. The pinned pre-open `q_live` population supplies listing identity. A runtime SQLite ledger is read-only for source certification. All generated output goes under the configured runtime root. No phase inserts into ClickHouse.

From the repository root, with the configured Python environment and completed source build:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v1/build_phase1.py preflight --date 2026-08-21
python -B research/rl_trading/v1/build_phase1.py benchmark --date 2026-08-21 --tickers AAPL SUGP --workers 2
python -B research/rl_trading/v1/build_phase1.py run --date 2026-08-21 --workers 4
python -B research/rl_trading/v1/build_phase2.py build --phase1 <completed-phase1-root>
python -B research/rl_trading/v1/build_phase3.py --phase2 <completed-phase2-root>
python -B research/rl_trading/v1/build_shards.py --phase3 <completed-phase3-root>
python -B research/rl_trading/v1/train.py --train-shards <chronological-train-shard-roots> --val-shards <later-validation-shard-roots> --run-name <name> --wandb-mode offline
python -B research/rl_trading/v1/evaluate_supervised.py --run <completed-train-run-root> --test-shards <later-held-out-shard-roots>
python -B research/rl_trading/v1/evaluate_replay.py --run <completed-train-run-root> --test-shards <later-held-out-shard-roots>
```

The Phase 1 launcher invokes Phase 2 for each completed session and records its path in the campaign summary. Phase 3 consumes that Phase 2 path. See [Phase 1](phase1.md), [Phase 2](phase2.md), and [Phase 3](phase3.md) for data contracts, output files, and restart behavior. The source manifest and ledger are selected with `--manifest` and `--ledger` when the runtime defaults do not apply. `STOP` markers and Ctrl+C drain admitted Phase 1 work; source/product integrity checks fail closed.

The Phase 3 V2 teacher admits new positions only among the top `--top-n` tickers by completed 60-second volume, while held tickers always remain visible. Search remains an approximate beam teacher unless a run proves exactness. The shard builder verifies the Phase 1–3 source chain and causal V7 certificates, then writes one immutable session shard: ticker identity, one-second feature banks, current top-N/held slots, action masks, account state, actions, and returns. It stores ticker histories once; the CUDA trainer assembles the configured history windows on the GPU. Shard construction fails closed when the causal V7 product is absent or uncertified.

Use disjoint chronological sessions for training and validation. `train.py` requires CUDA, uses a GPU-resident session bank, bfloat16, AdamW, a sample scheduler, optional Weights & Biases, metrics, restart checkpoints, and an observed GPU-compute fraction gate. `--max-steps`, `--allow-segment`, and `--no-require-gpu-bound` are for bounded smoke validation. Full training must use complete sessions and the default GPU-bound check. Model parameters and the shard contract are recorded in the runtime run manifest.

`evaluate_supervised.py` checks a separate test set and reports masked teacher action accuracy, trade recall, and return error. `evaluate_replay.py` uses the shard's certified current execution prices to replay the frozen model with its own cash, lots, dynamic top-N-plus-held view, and terminal liquidation. It reports profit, profit to initial cash, drawdown, trade counts, and the difference from the approximate Phase 3 teacher. The latter is a comparison, not an optimality bound. Both evaluators reject any test session present in the training or validation dates.
