# Strategy 1 fidelity audit and tensor-policy work

**The three-backtest comparison is not complete.** The existing V7 example
cannot be relabeled Strategy 1. New tensor policy reducers are implemented and
tested against the app, but their certified input adapter, lifecycle coordinator,
Portfolio/OMS and broker integration remain unfinished. No GPU session timing or
P&L for Strategy 1 has been produced.

## Verified application reference

The audit cold-read the completed run
`51dcacfb-bc9e-44a0-a9c8-8e2d2f103156` through the existing saved-review verifier.
The current published configuration matches the saved configuration hash:
`0a1eaf114247e1cc9026fc556090c720a618726931d585889312aa9b27e4d812`.
Its reconstructed 6,100-listing market plan also matches the saved plan token.
The reference uses a $10,000 account, Aug 18, 2026, 04:00–09:30 America/New_York,
and 100 ms strategy evaluation. Its financial/terminal heads both end at sequence
6,443.

| Backtest | Strategy clock | Broker clock | Open positions | Net P&L | Execution timing |
|---|---:|---:|---:|---:|---|
| Verified saved app Strategy 1 | 100 ms | 100 ms | 0 | $154.38046 | Not remeasured in this audit |
| Faithful GPU Strategy 1 | 100 ms | 1 s | Not executed | Not measured | Not measured |
| Faithful GPU Strategy 1 | 100 ms | 100 ms | Not executed | Not measured | Not measured |

The app reference has 26 closed episodes and $279.365 in fees. The earlier task
narrative records 80.65 s for this saved run; that is documentary historical
timing, not a new timing measurement. The audit's database/read time must never
be presented as the backtest's original runtime. Broker-resolution experiments
must hold the strategy clock at 100 ms and preserve population, candidate rules,
capital, configuration, source attempts and period. Aggregating broker evidence
to 1 s is an explicit new execution contract, not exact reproduction of 100 ms
order fills.

## Audit findings

| Concern | App Strategy 1 | Existing Torch ReplayRunner |
|---|---|---|
| Admission/data | Certified Strategy 1 candidates, frozen activation, confirmed BOS/HOD/pivots and causal V7 products | Released Early Squeeze funnel and generic projected fields |
| Geometry identity | Stable unified level and pivot identities | V6 nearest-slot ranks; identities can change between candles |
| Reentry | Prior filled-position high, closed clock and previous price-bearing 100 ms close | No completed-entry/reentry witness registers |
| Initial protection | Proposal carries completed-30s-low stop and third-overhead target into the bracket | First fill initializes broker return-based protection; rule amendments require an already-held quantity |
| Management | Ordered distinct resistance breaks, earned triple floors, upward-only target/stop amendments and broker acknowledgement | Generic SET_STOP/SET_TARGET proposals with direct state mutation |
| Funding | One-third mandate capital; three-position cap; sequential shared reservations | Fraction of current cash; competing buys are proportionally scaled at fill |
| Adds | At most three purchase groups; distinct eligible resistance and co-terminated bullish 100 ms/1 s evidence | Generic ADD_POSITION expression; no purchase-group or break-identity ledger |
| Fill evidence | Touch liquidity or price-specific eligible volume; persistent adaptive limit, protected OCA order groups; a new stop triggers first and fills in a later bucket | Interval VWAP participation and close-triggered protection exits |
| Fees | Cumulative order commission, including per-order minimum | Basis points on each filled notional |

These differences can change open positions and P&L. Matching the generic
Polars oracle proves that the earlier Torch engine implements its own approximate
contract; it does not prove compatibility with the application strategy.

## Implemented tensor reducers

`strategy_one.py` contains the first policy component layer:

- Entry/reentry admission over atomic `[B,N]` facts and post-broker financial
  state, including pending-order, permission, episode, quote and high-break gates.
- Ticker-owned resistance acceptance over stable-ID `[B,N,D]` geometry. New or
  moved levels cannot create retroactive crossings; missing seconds break
  continuity, and recrossing acceptance is separate from position-owned history.
- Position-owned distinct-ID history and disjoint three-break stop groups. A
  prefix scan assigns events to groups and masked reductions retain their lower
  band minima, including repeated witnesses and existing unfinished triples.
- Third/second/first ordinal targets, exact tick rounding, upward protection
  precedence, target-before-stop acknowledgement and earned-group retry state.
- Add admission for purchases two and three, using the app's co-terminated bar,
  MACD, distinct-resistance, financial and executable-bracket conditions.

Shapes and identities are explicit. There are no per-ticker/per-level Python
loops in these tensor functions, and no device scalar reads in their hot paths.
The tests use scalar reads only to compare outputs with the independent app
reducers. The protection function also passes a fullgraph `torch.compile` CUDA
comparison with two listings and two candidate accounts.

The `[6,4]` block array currently describes reducer stages and typed contracts.
It is a **prototype block manifest**, not the user's complete atomic
instruction/threshold strategy program. It has not been connected to
`compile_strategy`, `prepare_session` or `ReplayRunner`. The published app
strategy and its behavior were not modified.

## Remaining implementation and acceptance gates

1. Read the exact app-pinned candidate/activation/pivot/HOD/entry and V7 interval
   certificates. Encode full stable identities without rank substitution,
   truncation, fresh-source fallback or retrospective geometry. Bind all atomic
   field labels, units, availability times, constraints and thresholds to the
   strategy program/compiler.
2. Integrate filled-entry ownership, first-held-boundary exclusion, position
   highs, same-boundary retirement, pending resistance witness retries and
   refreshed financial state into the causal tensor coordinator.
3. Port one-third mandate admission, sequential reservations and position/risk
   limits. Preserve deterministic admission order rather than substituting
   proportional ticker allocation.
4. Port persistent adaptive orders, bracket/OCA state, quote and price-level
   capacity, later-bucket stop execution and per-order commissions. Define the
   1 s aggregation/delay rules separately from the matching 100 ms contract.
5. Validate a sampled and then complete native/100 ms tensor trace at proposal,
   reservation, command, fill, commission and financial-state boundaries. Final
   P&L alone is insufficient; first divergence must be inspectable.
6. Run the entire Aug 18 premarket with both broker clocks on the same 100 ms
   strategy schedule. Report every final open position, realized/unrealized/net
   P&L, fees and separate setup, execution and end-to-end timings. The current
   4 GiB dense tape guard must not be bypassed without a measured memory plan.

Until these gates pass, existing approximate replay timings must not be quoted
as Strategy 1 timings and neither GPU experiment can be called complete.

## Reproduce the read-only audit

From the repository root, use the configured laptop CUDA Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v1.torch_backtest.audit_strategy_one
```

Each invocation writes a new immutable run directory under
`D:/TradingML/runtimes/vectorized_backtest/strategy_one_audit`. The audited run
`025925d5348d4623849bb6eb48002edf` contains the reference definition, full
financial report, current release and an explicit incomplete-comparison report.
The launcher uses SELECT-only wrappers and never writes market or app journals.

Validation: 38 policy component tests passed, including CPU, laptop CUDA and
compiled CUDA comparisons. This is component evidence, not full-engine parity.
