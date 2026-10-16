# V24: global-only evolution scored by pure net profit

V24 implements the approved design independently of the running V23 campaign. There is one population evaluated across all training sessions in every generation, no per-session pretraining and no imported candidates or expert archives.

## Initialization and selection

Default normal population is 1,024. Generation 1 starts with 6,144 unique fresh random candidates (initial_multiplier=6), evaluated in bounded 512-member batches over the complete training cohort. Selection sees the entire pool, never separate batch winners. The next population contracts to 1,024; later generations remain 1,024. The multiplier is configurable 1..64, with initial size bounded at 65,536. Checkpoints bind both sizes and the multiplier; resume restores the exact population/RNG/IDs/pedigree and never repeats initialization.

V21 global-only breeding is retained: 102 unchanged score elites, 205 fresh, 359 score-tournament, 215 random-valid-parent and 143 elite-pair crossover slots at target 1,024. All mating respects five-link ancestry exclusion, deduplication and bounded fallback. Mutation covers every non-survivor, including fresh/fallback candidates, with V21's 20..85% generation rate and 1..5 effective edits. Financially valid losing and zero-trade candidates remain eligible; score zero may outrank losses. No minimum activity or positive-profit performance gate is introduced.

## Objective and execution

Score = sum of fee-adjusted session net profits. Cost = -score. There is no drawdown, ticker/session concentration, complexity, duration, exposure or other soft penalty, and no profit trimming. Configuration rejects nonzero trimming/penalty coefficients rather than silently accepting another objective. Every loss is counted. Drawdown, summed independent session drawdowns, profit excluding the best day/ticker, concentration, positions, activity and risk remain diagnostics only.

Execution retains $1,000 entries, $200 additions, $0.005/share commission with $1 minimum/order, and zero extra basis-point cost by default. Net profit includes those modeled costs; it does not promise unmodeled market-impact coverage. Existing causal data, terminal accounting checks, 20% maximum stop distance and $300 planned whole-position risk remain enforced. Additions are permitted only while the resulting entire position passes the existing checks; the 0..20 addition gene does not grant a new risk allowance per add.

## Rules and validation

Keep the expanded feature/expression capabilities, average-movement/close correction, uncentered temporal ratios, balanced numeric and rule sampling, and 128 nodes per rule. Initial requested rule-size bands remain 3..9 / 10..19 / 20..32; crossover may grow larger rules. Temporal context remains 120 observed candles. Feature semantics are versioned v24-magnitude-ratio-v1; original certified producers remain unchanged.

The same chronological 24 training / 12 validation sessions are intended. Validation evaluates up to 512 unique training-selected candidates, frozen before validation. V21's score/profit shortlist union is retained; since score equals profit, both orders coincide and remaining slots fill by that same training order. Validation does not alter selection, RNG or reproduction. Reused dates from older research do not imply that V24 imported candidates.

## Running and evidence

Default target: 50 generations. The single-stage entry points are research.vectorized_backtest.v24.run_search and its run_train alias. No campaign, monitor, workstation sync or predecessor qualification is installed by implementing this version.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v24.run_train `
  --training D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18/datasets/20261014-79261b811-24-12/enriched-ca3efc7c1/training.json `
  --validation D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18/datasets/20261014-79261b811-24-12/enriched-ca3efc7c1/validation.json `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v24/campaigns/fresh-run `
  --population 1024 --initial-multiplier 6 --generations 50 --batch-size 512 `
  --session-workers 2 --seed 182237 --maximum-gate-gib 8 --capture-gib 8
```

This command starts compute. Before a workstation launch, verify immutable committed/pushed source, byte-certified producers (including line endings), certified input receipts and released optimizer ownership. Generated output belongs only under the applicable runtime root. Tests cover pure-profit arithmetic, zero/loss eligibility, risk rejection, the sixfold complete-cohort evaluation and contraction, validation independence, sealed stop/resume without reinitialization, tamper rejection, 128-node boundaries and literal/feature semantics. These are correctness checks, not campaign throughput or profitability evidence.
