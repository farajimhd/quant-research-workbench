# RL trading V1

This package builds a three-phase hindsight dataset for long-only portfolio teacher trajectories. Phase 1 reads certified completed 100 ms price bars and 1 s MACD/activity from `arte`, validates their build certificates, and labels price-action opportunities. It reads the pinned pre-open population from `q_live` for listing identity. Phase 2 compiles the full market-wide holding values and sparse liquidity-gated opening values. Phase 3 searches cash-constrained long-only trajectories using the Phase 2 tensor.

ClickHouse access is read-only and limited to the two `arte` products, the certified `q_live` population, and storage metadata. A runtime SQLite ledger is read-only for source certification. Phase 3 writes its own restart checkpoint as a runtime file, and all dataset output goes under the configured runtime root. No phase inserts into ClickHouse.

From the repository root, with the configured Python environment and completed source build:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v1/build_phase1.py preflight --date 2026-08-21
python -B research/rl_trading/v1/build_phase1.py benchmark --date 2026-08-21 --tickers AAPL SUGP --workers 2
python -B research/rl_trading/v1/build_phase1.py run --date 2026-08-21 --workers 4
python -B research/rl_trading/v1/build_phase2.py build --phase1 <completed-phase1-root>
python -B research/rl_trading/v1/build_phase3.py --phase2 <completed-phase2-root>
```

The Phase 1 launcher invokes Phase 2 for each completed session and records its path in the campaign summary. Phase 3 consumes that Phase 2 path. See [Phase 1](phase1.md), [Phase 2](phase2.md), and [Phase 3](phase3.md) for data contracts, output files, and restart behavior. The source manifest and ledger are selected with `--manifest` and `--ledger` when the runtime defaults do not apply. `STOP` markers and Ctrl+C drain admitted Phase 1 work; source/product integrity checks fail closed.

Version V1 here describes package organization. Existing label version identifiers and output schemas remain unchanged; code hashes produce new immutable run identities.
