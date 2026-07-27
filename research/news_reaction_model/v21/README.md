# News Reaction Model V21

V21 is a controlled successor to V20. It keeps the certified V18.3 episode rows,
the V15 causal prior-news arrays, and the complete V20 encoder unchanged. It
replaces only the output formulation, loss, metrics, selection contract, and
training termination behavior.

## Why V21 exists

V20's 19-class signed-return distribution slightly improved direction
classification, but its expected signed return was worse than the training
median baseline. A single flat distribution forced one head to learn:

- whether an opportunity is neutral, upside, or downside;
- the magnitude conditional on an opportunity existing;
- highly imbalanced tails.

V21 factorizes the same normalized forecast:

```text
P(direction, magnitude)
  = P(direction)
    × P(magnitude bucket | direction)
```

The direction classes are:

```text
neutral, upside, downside
```

Upside and downside each have nine positive-magnitude buckets:

```text
[0,0.5), [0.5,1), [1,2), [2,5), [5,10),
[10,20), [20,50), [50,100), [100,+inf)
```

The authoritative V18 direction remains unchanged. Upside magnitude is the V18
episode high return; downside magnitude is the absolute V18 episode low return;
neutral has no magnitude target. Multiplying the two distributions reconstructs
one 19-state signed-return distribution exactly, so direction and return cannot
contradict one another.

## Outputs

For each article V21 emits:

- `P(neutral)`, `P(upside)`, and `P(downside)`;
- `P(magnitude bucket | upside)`;
- `P(magnitude bucket | downside)`;
- expected upside and downside excursion percentages;
- unconditional expected signed excursion:

```text
P(upside) × E[magnitude | upside]
  - P(downside) × E[magnitude | downside]
```

## Training behavior

The default encoder remains the 68.8M-class V20 architecture:

- width 768;
- 12 attention heads;
- four current layers and two causal-prior layers;
- two cross-attention blocks;
- six sparse experts with top-2 routing;
- batch size 2,048.

The default maximum is 50 epochs, but validation early stopping is authoritative:

- minimum eight completed epochs;
- patience of six epochs;
- minimum score improvement of `1e-4`;
- `best_val.pt` remains the only selected model;
- `latest.pt` preserves interruption/resume state.

This addresses V20's confirmed pattern: its best checkpoint occurred at epoch 5
while validation quality degraded severely through epoch 50.

## Data

No dataset build is required. V21 opens these products read-only:

```text
D:\market-data\prepared\news_reaction_model\v18\single_ticker_episodes_v1
D:\market-data\prepared\news_reaction_model\v15\causal_context_v1
```

The chronological split remains:

- training: 2019 through 2025;
- validation: 2026.

## Workstation command

```powershell
conda activate ml4t
cd D:\TradingML\codes\news-reaction-model\v21
python -m research.news_reaction_model.v21.run_train
```

Evaluate a selected checkpoint:

```powershell
python -m research.news_reaction_model.v21.run_evaluate `
  --checkpoint D:\TradingML\runtimes\news-reaction-model\v21\train\single-ticker-episodes\<run>\checkpoints\best_val.pt
```

V21 uses the same `news-reaction-model-v3` W&B project as V19 and V20.
