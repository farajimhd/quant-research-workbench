# V28: independent action-network evolution

Each strategy owns five separate sparse feed-forward tanh graphs: entry, exit,
add, reduce, trail. There is no symbolic-rule or V27 crossover path. The graphs
are evaluated jointly by the existing causal, protected financial replay.

## Search contract

- Every neuron is tanh(bias + weighted sum). Hidden values stay continuous;
  only each action's final output is thresholded.
- Each action proposes inheritance/exchange/composition at 40/30/30 percent.
  One base parent supplies its cooperating action/policy set; one or two actions
  receive donor networks, exchange or composition. Other actions stay intact.
  Composition chooses complete or internal modules equally. Whole-network
  composition mixes margins around the parents' learned thresholds with a
  dominant primary and weaker secondary term. Local edits retain thresholds;
  thresholds remain mutable. At most one policy gene or coupled swing pair is
  exchanged. Illegal size/depth proposals are rejected, counted and retried
  within that action before rejecting the child. No graph is truncated to fit.
- Up to 64 reachable neurons per action, fan-in 8, depth 16: at most 320 neurons
  and 2,560 weighted connections per strategy. These are finite compute bounds,
  not a claim to represent every equation. Initial sizes stratify 1..16 plus
  extra simple atoms and 20/24/32/48/64-node graphs independently per action,
  with varying depth/branching and family-balanced feature sampling. Every leaf contains a market-value input;
  management actions can additionally read held-state features.
- Initial weights/biases uniform [-1,1]; evolved bounds [-4,4]. Final thresholds
  uniform inside (-1,1), inherited with an intact output and retained during
  local edits. Parameter edits: 80% reflected local Gaussian (sigma .2; .1 for
  thresholds), 20% uniform reset. Structural edits add/remove/change connectivity,
  grow a tanh output or retain an internal module.
- Keep 10% elites, 20% fresh and remaining mating slots, five-link ancestry,
  score/random/elite parent routes, training-only behavior pairing in thirds,
  and the 20..85% mutation mask with 1..5 distinct edits. Each mutation proposal
  chooses parameter/structure at 70/30. All policy genes, including fixed/swing
  stop mode and both coupled swing windows, remain evolvable.
- Local objective P-.25W-.05D; global sum(P)-.25max(sum(W),W_session)-.05sum(D).
  Original positive-profit archives, fees, $1,000 entry/$200 add, $300 planned
  risk and 20% maximum stop distance remain. Validation never affects reproduction.

## Inputs, precision and execution

All certified shard channels remain available through three input forms:
absolute value (tanh for ratio/bool, signed log1p divided by four then tanh for
dimensional values), current-clock cross-sectional z-score followed by tanh(z/2),
and explicit validity. Sampling chooses these at 45/45/10 percent. Cross-sectional
statistics use only finite certified available values at the same causal clock;
no future clocks, full-session fitted statistics or validation data are used.
Absolute channels retain levels that normalization removes. Missing value
channels are zero; their validity channel is zero. Other observed inputs can
still drive the graph, but an action cannot fire with only missing market
values or state/validity inputs: at least one reachable market-value ancestor
must be observed. This changes neural missing-input semantics, not certification
or risk checks. Neural arithmetic is float32; financial arithmetic stays float64.
Position-state features use the state before
the next execution: held flag, unrealized fractional profit, age/3600,
capital/$1,000, stop distance, additions/20, time since management/300. Entry
cannot read held-state features. Invalid market inputs cannot become true
conditions through bias alone.

Network evaluation is fused into chronological CUDA-graph replay. Packed sparse
graphs run in a Triton kernel; market projections are shared across candidates
and action bits are packed in one kernel. No population × session × hidden-node
tape or precomputed dynamic-state gates are allocated. A bounded one-clock CUDA
neuron workspace uses a power-of-two bucket for the batch's largest live graph
and direct indexing instead of repeated register-array gathers. Feature reads
are contiguous across listings. State is not recurrent:
historical context comes from certified features and explicit position state.

Exact certificates are retained. Python 3.12 reproduces the producer's Python
3.13 AST serialization for enriched extractor identity; original hashes must
match. The CRLF-bound history producer is copied byte-exact into the laptop
runtime code snapshot; repository producer files and certificates are unchanged.

## Launching

Run with `PYTHONDONTWRITEBYTECODE=1` and Python `-B`. The laptop job uses the
certified runtime code snapshot to retain raw producer identities. All output
must be under `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28`.

```powershell
python -B -u -m research.vectorized_backtest.v28.run_train `
  --mode local --authorize-run --day 2026-08-04 --generations 30 `
  --population 1024 --initial-multiplier 6 --batch-size 512 --seed 2020 `
  --training D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28/datasets/aug04/training.json `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28/campaigns/aug04-local30 `
  --commit <qualified-commit>
```

The local-only command does not open validation or start Phase 2. `--mode full`
requires chronological certified 24-training/12-validation manifests, completes
all local searches and seeds global optimization exclusively from V28 local
archives plus fresh V28 individuals. Both phases have source/input-bound exact
checkpoint resume. Ctrl+C requests a sealed-generation stop; remove STOP only
when deliberately resuming that exact campaign.

Laptop defaults: one worker, no prefetch, 12 GiB host reserve plus 8 GiB load
admission. Full multi-session host/GPU sizing requires its own qualification;
the Aug4 local run does not qualify workstation full-run throughput.

`profile.py` performs finite real-input CPU/CUDA financial checks and repeatable
cold/warm timing with saved metric hashes. `--clocks 19800` covers the full Aug4
session. Bounded prefixes are explicitly diagnostic, never counted as training.
`tests/test_vector_v28.py` covers generic genetics, invalid graphs/masks,
deterministic RNG, CPU/CUDA eager/captured replay, sealed local resume, Phase-2
seed provenance and validation-independent global reproduction.
