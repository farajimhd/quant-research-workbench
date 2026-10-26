# V54: shared recurrent strategy with six action heads

V54 evaluates one 64-unit sparse tanh recurrent core per candidate and listing,
then reads entry, exit, add, reduce, trail, and continuous stop-loss heads.
Every core cell reads current causal market/position/previous-fill channels and
preceding-clock latent cells. All six readouts use that same current latent state.
There are no current-clock cycles. Memory resets per session and survives CUDA
replay blocks; export/restore preserves logical rather than physical buffers.

The stop head requests a distance of 0.1% to 20% below the previous causal mark.
At entry it sets the initial ask-relative stop (or tightens a configured swing
stop); while held it can only tighten. Unknown/invalid stop output retains the
ordinary policy stop. Invalid quotes cannot apply the learned adjustment.
This does not guarantee a $300 realized loss in a market gap: it preserves the
V53 prospective position-risk admission cap including commissions and costs.
Entry/add caps remain $1000/$200; one-second valid bid/ask execution and forced
terminal liquidation rules are unchanged.

The completed pilot exposed an inherited accounting limitation: very small
target reductions can pay a $1 minimum commission larger than their profit
and stop-risk relief. Entry/add admission remains bounded, but accumulated
reduction fees can raise the observed paid-cost risk above $300. The unchanged
eligibility filter rejects such candidates. Two generation-2 candidates were
rejected at peaks of $300.99 and $302.27; replaying their first crossing events
through the V53 financial oracle produced identical results. This is not an
every-action hard cap. Adding prospective reduction-fee admission requires a
separate execution change and a fresh comparison experiment.

Fitness is exactly V53: net P&L - gross losing-position dollars - 2 * equity
maximum drawdown, including open-position marks. No validation is opened.
The experiment is laptop-only, 2026-08-13, seed 2021, 6144 fresh founders,
population 1024, ten generations. Founders differ from V53 because the genome
architecture differs. Half use newly constructed temporal feature-family priors
with the same family allocation; half are random. These are not the original
V53 seed graphs or old winners. Threshold calibration uses 64 training-only
reset-state cross-sections and flat/held state inputs, never profit or validation.

Core crossover chooses aligned cell genes and each of the six heads from its
parents. The fixed 64-cell layout makes references valid, but differing parent
representations mean useful behavior is not guaranteed to transfer. The 25%
Boolean crossover allocation composes one discrete head using OR/AND/XOR/XNOR
in proportions 40/20/20/20 over the base parent's core. It preserves both readout
branches, not both parent core computations. Nested compositions are explicitly
rejected at two branches, then use the recorded V53 parent-mutation fallback.
Stop remains continuous. No mutation is applied immediately to accepted crosses.
The other V53 elite/fresh proportions, active-parent eligibility, ancestry,
training-only species, checkpoint seals and independent 32-child mutation probe
remain. Fixed-width structural mutations rewire connections; cells do not grow
or disappear. Thus 64 recurrent cells + 6..11 readouts + 0..5 logical joins stay
below the unchanged 512-node strategy ceiling. Complexity also counts edges.

The CPU oracle updates cells and heads with float32 arithmetic; finance remains
float64. Optimized CUDA uses parallel sparse cells followed by fused head reads,
with device-side ping-pong phase advancement. Memory is bounded at 4 GiB.
`profile_shared` compares the vectorized Torch oracle and CUDA tiles at the
actual Aug13 listing width. Its synthetic neural timings exclude projection,
finance and initialization; full-session qualification is separate.

Run through a committed immutable deployment, from its code directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v54.profile_shared --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/qualification/profile
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v54.full_launch --source-job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v49/full-82a7869df-p1024-s2020-l050-dd020/full-job.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/aug13-g10 --code D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/code-VERIFIED_COMMIT --generations 10
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v54.pilot --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v54/aug13-g10/pilot-job.json
```

Replace VERIFIED_COMMIT with the actual verified deployment name. The scheduled
runtime launcher pins deployment hashes and exposes an independent progress
terminal. Never run concurrently with another laptop GPU optimizer. Generated
profiles, compiler caches, ledgers, checkpoints and reports belong under runtime.
