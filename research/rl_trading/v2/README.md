# V2: reward-driven portfolio PPO

V2 starts a stochastic policy from random weights and trains on its own simulated
account trajectories. There are no teacher actions, hindsight targets, or teacher
account states. Full extraction has no V1 Phase 1–3 dependency; optional V1 cache
reuse reads its certified provenance metadata. V1 source and runtime are unchanged.
This is a research implementation, not a released strategy or profitability claim.

The single-account full-session experiment uses `--environments 1`,
`--capital-multipliers 1`, `--session-order cycle`,
`--min-completed-episodes 3`, and `--selection-min-episodes 1`. It replays the
training dates in supplied chronological order at the configured initial cash.
`--stream-sessions` retains only the active training bank in memory, releases it
during validation, then restores the account against its certified bank. The
stored plan/certificate is checked first,
and each full array is hash-verified before its session is read. This avoids
mapping all four large market banks throughout the run.
The builder performs the exhaustive finite/nonnegative array scan before it
publishes `complete.json`; loading an exact SHA-256-matched certified bank does
not repeat that memory-intensive scan. Shapes, types, identities, clock, and
certificate hashes remain checked on every load.
PPO still updates from bounded consecutive one-second segments; the same account
continues across those updates until the session ends. The iteration budget must
cover the required complete sessions. Validation cannot select a checkpoint
before the first completed training session. Episode and validation summaries
separate policy pass/buy/reduce/close decisions, discretionary fills/costs, and
realized P&L from discretionary versus mandatory exits. These are diagnostics;
the reward remains net change in account equity without an additional fee tax.
Validation records an unfilled end-of-session position as an invalid terminal
rollout; such a checkpoint cannot be selected, even if its marked equity is high.
Training still stops if its own account cannot finish flat, since resetting that
account would falsely assume an executable liquidation.

To extend a completed one-account campaign to more full passes, start a new
versioned run with a larger `--min-completed-episodes`, a sufficient
`--iterations` budget, and `--continue-from-run <completed-run-root>`. This
verifies the parent data, model, execution code, configuration, and checkpoint
before carrying forward policy, optimizer, RNG, account, session cursor, and
the validation-best checkpoint. The new run records its parent checkpoint hash
and gets its own W&B history. Ordinary `--resume` continues an interrupted
run under its unchanged contract and W&B ID; it does not change the required
number of sessions. Repeated passes use `--session-order cycle` and remain
subject to later-date validation, since training return alone cannot establish
convergence or generalization.

## Market and observation contract

`build_data.py` reads the full population of a certified ARTE market-day build,
with stable listing IDs, completed one-second bars/indicators, prior-session V7,
and identity-matched point-in-time reference features. It reuses V1's certified
observation extraction functions unchanged and hashes their code. Database reads
go through `ArteReader` with `readonly=1`, table-policy and actual part-placement
checks. No flatfile/event fallback or ClickHouse writes are permitted.
Missing V7 certification fails the build by default; certified empty V7 is
allowed. An explicit dated `--v7-exclusions` document can name the exact
missing ticker/listing identity with reason `unresolved_identity_no_certified_v7`
and evidence. The builder requires its entries to match the entire missing-seed
set and pins its hash and entries in the V2 plan. This allows the documented
LGHL identity exception to be visible rather than silently omitted. Extraction
never requires a teacher population.
The market-day certificate explicitly records tradable names with no canonical
events; V2 requires every certified event-bearing listing and records the
no-event count instead of inventing one-second bars for those names.

The builder automatically discovers completed V1 banks under the configured
local runtime's `rl-trading-shards/<date>/*`. `--v1-shards <paths...>` selects
explicit banks or account/cost overlays instead. It checks the source build and
attempts, full listing identity, feature schema, observation code hashes, and
market array certificates. Source hashes may differ only by LF/CRLF line endings;
the normalized bytes must reproduce the certified hash. Incompatible versions are reported and extracted
afresh; corrupted certificates fail the build. It never reads teacher arrays.
The selected V1 bank's linked Phase 1/2 plans, completion certificates and listing
progress metadata must remain accessible while validating the cache.
Compatible `features.npy` and `volume_60s.npy` rows are copied into independent
V2 banks. V1 remains read-only; the finished V2 bank does not need V1 to train.
The full V2 population is retained even when V1 covers only a subset.

Cached rows skip indicator/reference extraction and V7 computation. They still
fetch exact one-second open and close, price validity, volume and trade counts from the
pinned ARTE bars, because V1 does not store those raw execution fields together
losslessly. V2 does not invert rounded log features or substitute V1 execution
prices. Missing listings use full extraction. The first certified 1-second
technical row also supplies the prior-session close when the market-day builder
has a carried prior seed; an absent/zero seed blocks regular-session entry.

`--workers 2` defaults to two spawned listing processes (range 1–16), each with
`--query-threads 2` (range 1–4). At most one task per worker is admitted; results
are bounded and the parent owns all V2 writes and checkpoints. Each task carries
only its listing's source unit. Array copies, price carrying and rolling activity
use NumPy operations; causal V7 updates remain sequential within each uncached
listing. `STOP` stops admission, joins running workers, and preserves certified
completed rows. Progress distinguishes V1 copies, new extraction, resumed rows,
queued/active work and failures. Worker count does not change dataset identity.
Actual full-market throughput has not been benchmarked on the busy workstation.

Every second, liquid listings are ranked by completed trailing 60-second share
volume with a stable listing-ID tie break. Default liquidity requires 20,000
shares and 11 trades in that window and a price no more than 5 seconds old.
The policy sees top 1000 candidates plus every held listing outside that set.
Only ranks 1–900 can increase exposure. Ranks 901–1000 can hold/reduce/close.
Leaving the top 1000 issues a sticky mandatory liquidation, even on later reentry.
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
distribution for continuous size, plus two Beta distributions for stop and
profit-target distances on new entries. A buy proposes a fraction of remaining
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
The default commission profile is IBKR Pro Fixed US SmartRouting, pinned to
2026-09-26: $0.005/share, $1 minimum, capped at 1% of notional (the cap overrides
the minimum). Add SEC sell fees of 0.0000206 of notional, FINRA TAF sell fees of
$0.000195/share capped at $9.79 per simulated execution, and CAT $0.000003/share
on both sides. Source: [IBKR published schedule](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php).
This is a current-cost replay scenario, not historical invoice reconstruction.
Components are unrounded; invoice aggregation, taxes and routing-specific extras
are not reconstructed. `--extra-venue-fee-per-share` supports explicit stress costs.
The generic fee options below require `--commission-model research` and cannot
silently add to the IBKR profile. Slippage remains **uncalibrated**:

| Setting | Default | Meaning |
|---|---:|---|
| `--fee-ratio` | 0 | Research-only notional fee, each side |
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
research_fee = max(minimum_fee, q * fill_price * fee_ratio + q * fee_per_share)
```

An order chosen after second t is simulated against the first trade price of
the fresh t+1 bar, adjusted by the slippage model. The t+1 close is used only
to mark equity after the transition; it does not set the fill price. This is a
one-second delayed price-bar approximation, not reconstructed NBBO, an actual
broker IOC fill, or order-book matching. Arrival volume is used only by the execution transition,
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

The policy scores every visible ticker and first samples a learned trade/pass
gate. If trading, it selects one eligible ticker and buy, reduce, or close
mode, then samples its allocation and, for a new position, stop and target.
This limits discretionary fills to one order per second; sticky forced exits
can still execute together. The gate begins with a sparse-trading prior but
is trainable. PPO uses the selected action's probability and the full
net-account advantage, so its KL limit no longer scales with 1000 independent
action samples. Each step's marked price movement, fill slippage, and cash
fees are also attributed to their ticker, and their sum must reconcile with
the account reward. This ledger is diagnostic and does not supply labels.
The earlier independent-action campaigns are retained as failed experiments:
their many simultaneous small orders incurred prohibitive fixed commissions.

## Learned exits and estimated regular-session bands

On entry the policy samples stop distance (default range 0.1%–50%) and target
distance (0.1%–200%) relative to actual entry fill. These configurable bounds
parameterize the action; they are not trained optimal settings. Adding shares
preserves the original prices and entry clock. Completed one-second closes
trigger a sticky exit, attempted on the next second with ordinary caps, costs,
and partial fills. These are sampled-price triggers, not broker-native stop or
limit orders; intrasecond crossings are not modeled. Gaps can exceed the stop
and target-triggered sales may fill below the target. PPO learns both distances
from net account rewards. No holding-age penalty is applied; gamma remains 1.
A smaller gamma discounts future rewards but cannot enforce a maximum hold.

This version does not require a halt-status sidecar. During 09:30–16:00 ET it
blocks new entries for listings whose certified preceding close is below $0.75
or unavailable. A research band reference starts at that prior close and then
uses a trailing five-minute volume-weighted average of *completed* regular
one-second closes. The estimated bounds are 5% around that reference, with a
0.5% inward buffer for stop and target levels. The policy samples both bracket
distances, but a regular-session entry and its effective stop/target must fit
inside the current estimated bounds. Existing long positions can still exit
when the prior close was below $0.75. Effective thresholds are clipped again
as the estimated reference changes; original learned distances are retained.

These are **not official SIP LULD bands**: one-second close/volume aggregates
cannot reproduce the eligible-transaction mean, official opening anchor,
security tier, published updates, or an exchange trading pause. The price-only
simulator requires a fresh eligible bar and positive volume to fill; absence of
prints is not labeled as a halt. Stops/targets are next-second simulated exits,
not broker-held protection and cannot guarantee liquidation through a halt.
Report any backtest outcome as an estimated-band research result, not an
executable or halt-safe return. The separate status-sidecar reader remains
available for a later certified-status dataset but is not used in this version.

## Laptop commands

Use the configured Python environment with NumPy, Polars, PyTorch, and repository
data-source dependencies. All launchers disable bytecode. Nothing starts or
synchronizes workstation services. The runtime root must already exist.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$py = 'C:\Users\g835l\miniconda3\envs\ml4t\python.exe'
# These laptop commands issue bounded read-only queries against the certified
# ARTE endpoint; they start no process on the workstation.
& $py -B research/rl_trading/v2/build_data.py --date 2026-08-20 --manifest <local-build-manifest> --ledger <local-build-ledger> --workers 2 --query-threads 2
& $py -B research/rl_trading/v2/run_train.py --train-sessions <earlier-v2-session-roots> --val-sessions <later-v2-session-roots> --run-name ppo-v2-seed17 --device cpu
& $py -B research/rl_trading/v2/evaluate.py --run <v2-run-root> --test-sessions <strictly-later-v2-session-roots> --device cpu
```

For a laptop GPU campaign with regular W&B uploads, use `--device cuda
--wandb-mode online --wandb-project rl-trading-v2`. The launcher discovers a
local W&B credential from the configured environment/secrets locations and
fails before training if it is absent. After each committed iteration it logs
losses, completed-episode return/drawdown/fees and scheduled validation
return/drawdown/fees. Each iteration remains in `metrics/*.json`; a durable sync
cursor replays metrics missed by W&B after a checkpointed crash. Resuming uses
the same W&B run ID and requires the original run contract. W&B files stay under
the run's external `wandb/` directory, and the API key is not stored in manifests.

Training defaults to 1,000 iterations, four bounded environments, 512 rollout seconds, four PPO
epochs, 32-row minibatches, and a small 64-wide encoder. Capital varies across
0.5x/1x/2x the configured initial balance in training. Use `--capital-multipliers`
to specify the intended range. Validation uses the configured initial balance.
Validation runs at iteration 1, every 100 iterations, and the final iteration.
It uses three fixed-seed stochastic rollouts per session, matching the sampled
training policy while preserving training RNG state; selection uses their mean
net return and reports fill count and fees. GAE uses gamma=1 and lambda=1 so
observed rewards receive full weight within the 512-second rollout; the critic
still bootstraps beyond that horizon.
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
