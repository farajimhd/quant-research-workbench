# V2: reward-driven portfolio PPO

V2 starts a stochastic policy from random weights and trains on its own simulated
account trajectories. There are no teacher actions, hindsight targets, teacher
account states, or V1 Phase 1–3 dependencies. V1 source and runtime are unchanged.
This is a research implementation, not a released strategy or profitability claim.

## Market and observation contract

`build_data.py` reads the full population of a certified ARTE market-day build,
with stable listing IDs, completed one-second bars/indicators, prior-session V7,
and identity-matched point-in-time reference features. It reuses V1's certified
observation extraction functions unchanged and hashes their code. Database reads
go through `ArteReader` with `readonly=1`, table-policy and actual part-placement
checks. No flatfile/event fallback or ClickHouse writes are permitted.
Missing V7 certification fails the build instead of silently excluding a listing;
certified empty V7 is allowed. Extraction never requires a teacher population.

Every second, liquid listings are ranked by completed trailing 60-second share
volume with a stable listing-ID tie break. Default liquidity requires 20,000
shares and 11 trades in that window and a price no more than 5 seconds old.
The policy sees top 120 candidates plus every held listing outside that set.
Only ranks 1–100 can increase exposure. Ranks 101–120 can hold/reduce/close.
Leaving the top 120 issues a sticky mandatory liquidation, even on later reentry.
Falling below the liquidity gate also removes a listing from the eligible set.

History is gathered by listing identity, never by yesterday's or last second's
rank slot. Shared temporal convolutions and cross-market attention have no slot
or ticker embeddings. Reordering inputs reorders the corresponding outputs.
Account inputs are cash/equity, exposure/equity, return on initial equity,
drawdown, session time remaining, and forced-exit exposure/equity. Per-listing
inputs include position weight, unrealized return, rank, forced-exit state, and
capacity ratios (shares/recent volume and equity/recent dollar volume).

## Sizing, caps, and action probabilities

Each listing has categorical probabilities for hold/buy/reduce/close and a Beta
distribution for continuous size. A buy proposes a fraction of remaining
per-ticker allocation capacity; a reduction proposes a fraction of held shares.
Joint buy demands are proportionally scaled to available cash. Sell proceeds
are not assumed available at decision time. Whole-share execution rounds down.
The default `--max-ticker-weight 1` permits concentration up to account equity;
no smaller percentage limit was specified. A smaller cap is configurable.
Mark-to-market cap breaches block increases but do not force trimming.

| Reference price (USD) | Maximum shares per order | Notional at maximum shares |
|---|---:|---:|
| Below 1 | 40,000 | Below $40,000 |
| 1 through 5 | 35,000 | $35,000–$175,000 |
| Above 5 through 10 | 30,000 | Above $150,000 through $300,000 |
| Above 10 through 20 | 25,000 | Above $250,000 through $500,000 |
| Above 20 through 50 | 20,000 | Above $400,000 through $1,000,000 |
| Above 50 | 15,000 | Above $750,000, no fixed dollar ceiling |

These are per-order caps, not maximum total position sizes. One net instruction
per listing per second is allowed. The stricter decision/arrival price-band cap
applies if the price changes bands. The model's original sampled mode/size and
their joint log probability are stored for PPO. Budget transforms and execution
are environment mechanics; clipped fills are never substituted into the PPO
probability ratio.

## Costs, slippage, latency, and liquidation

Ratios are decimal fractions: `0.001 = 0.1% = 10 basis points`.
Defaults are **uncalibrated price-only research assumptions**, not broker quotes:

| Setting | Default | Meaning |
|---|---:|---|
| `--fee-ratio` | 0.0001 | 1 bp of fill notional, each side |
| `--fee-per-share` | 0 | Optional additional per-share fee |
| `--minimum-fee` | 0 | Optional per-filled-order fee floor |
| `--base-slippage-ratio` | 0.0005 | 5 bps each side, including assumed half-spread |
| `--impact-ratio` | 0.001 | Coefficient on square root of recent participation |
| `--volatility-slippage-ratio` | 0.1 | Coefficient on recent one-second log-return volatility |
| `--max-volume-participation` | 0.1 | Maximum 10% of the arrival second's observed volume |

For reference price P and filled quantity q:

```
participation = (own filled shares in prior 60 seconds + q) / max(completed trailing 60s volume, 1)
slippage_ratio = base + impact_coefficient * sqrt(participation) + volatility_coefficient * recent_volatility
buy_price  = P * (1 + slippage_ratio)
sell_price = P * (1 - slippage_ratio)
fee = max(minimum_fee, q * fill_price * fee_ratio + q * fee_per_share)
```

An order chosen after second t executes against a fresh completed price at t+1;
the observed t close is never used as a guaranteed same-time fill. This is an
explicit one-second delayed price-bar approximation, not reconstructed NBBO or
order-book matching. Arrival volume is used only by the execution transition,
never exposed early to the policy. Unfilled IOC quantities expire after that
transition; forced exits are reissued until flat. There are no resting policy
orders between decisions, so no hidden pending cash reservations. Consecutive
orders share a 60-second impact history, including both buys and sells.

Missing fresh price or zero arrival volume produces no fill. Exits obey the same
caps, costs, and participation limits. Mandatory end-of-session liquidation
starts at 19:58 ET; simulation continues to 20:00 ET to attempt execution. If
holdings remain, the episode is explicitly invalid and training/evaluation
fails rather than fabricating terminal proceeds. This changes V1's terminal
contract and is isolated in V2. Cost coefficients producing >=100% slippage also
fail closed. Impact does not alter the external market tape: this remains a
bounded price-taking approximation requiring independent capacity validation.

Reward is `(equity_next - equity_now) / initial_equity`, marked at reference
prices. Fill-price slippage and cash fees enter exactly once. Summed rewards
reconcile with terminal net profit / initial equity. PPO uses gamma=1 and GAE;
rollout chunks bootstrap the critic and do not reset account state. Both training
and validation report fees, slippage dollars/ratios, partial/unfilled orders,
forced fills, turnover notional, net return, and drawdown.

## Laptop commands

Use the configured Python environment with NumPy, Polars, PyTorch, and repository
data-source dependencies. All launchers disable bytecode. Nothing starts or
synchronizes workstation services. The runtime root must already exist.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$py = 'C:\Users\g835l\miniconda3\envs\ml4t\python.exe'
# These extraction commands query the configured ARTE endpoint: do not run them
# against the busy workstation during V1 training. Use certified local sessions.
& $py -B research/rl_trading/v2/build_data.py --date 2026-08-20 --manifest <local-build-manifest> --ledger <local-build-ledger>
& $py -B research/rl_trading/v2/run_train.py --train-sessions <earlier-v2-session-roots> --val-sessions <later-v2-session-roots> --run-name ppo-v2-seed17 --device cpu
& $py -B research/rl_trading/v2/evaluate.py --run <v2-run-root> --test-sessions <strictly-later-v2-session-roots> --device cpu
```

Training defaults to 1,000 iterations, four bounded environments, 256 rollout seconds, four PPO
epochs, 32-row minibatches, and a small 64-wide encoder. Capital varies across
0.5x/1x/2x the configured initial balance in training. Use `--capital-multipliers`
to specify the intended range. Validation uses the configured initial balance.
Validation runs at iteration 1, every 100 iterations, and the final iteration.
The CPU default supports laptop checks; CUDA is opt-in. Benchmark before scaling:
the reference simulator is NumPy/CPU and is not claimed GPU-bound.

Artifacts live under `<runtime>/rl-trading/v2/`. The market builder checkpoints
each listing with array hashes and publishes completion only after validation.
`STOP` in a build directory stops before the next listing. Training atomically
checkpoints completed PPO iterations, optimizer, accounts, session selections,
and RNG states. `--resume` requires identical source/data/model/execution/optimizer
contracts; only the requested total iteration count may increase. `STOP` in a
training run stops at the next iteration boundary; remove it to resume. An
interrupted partial iteration is replayed from the preceding checkpoint.

Validation sessions must be strictly later than training; held-out evaluation
must be strictly later than both, with an identical feature schema. Full sessions
are required unless `--allow-segment` is explicitly used for smoke tests. The best
checkpoint uses validation mean net return, not teacher agreement. Model selection
is not held-out evidence. Run multiple seeds and cost stress scenarios before
acceptance; neither defaults nor synthetic tests establish trading profitability.
