# V16: independent global and session paths

V16 is copied from V15. Assumed-fill accounting, fees, causal execution, price
filter then top10 funnel, immutable input certificates and GPU replay are retained.
The initial population is 1024 unique random strategies (seed 162237); no previous
strategies, optimization scores, RNG or result caches are imported.

## Objectives

Minimize global cost:
`-total_net_profit + .25*positive_session_concentration
 - .10*N*worst_20_percent_session_mean
 + .05*sum_drawdown + .005*sum_risk_dollar_hours
 + .0004*sum_capital_dollar_hours`.
Concentration is sum(positive session profit squared)/sum(positive session profit),
zero when the denominator is zero. Fractional tails include inactive sessions.
Local cost for session s is independently:
`-session_net_profit + .05*session_drawdown + .005*session_risk_dollar_hours
 + .0004*session_capital_dollar_hours`.
No complexity, shortfall, profit trimming or worst-trade penalty is applied.
Validation results never enter either objective, selection or training RNG.

## Population budgets and mutation

For 1024 members, global/local paths each produce 512 slots. Global: 51 unchanged
elites, 52 fresh immigrants, 409 tournament-selected crossover offspring. Local:
up to 30 distinct unchanged champions, up to 150 champion crossovers (five random
distinct partner sessions each), remaining slots intrasession crossover, and 102
fresh immigrants. A local champion already preserved globally occupies one slot;
its local slot becomes offspring. Parent pools are 13 candidates/session. All
crossovers obey five parent-link-generation ancestry exclusions. Missing unrelated
mates use explicitly counted random immigrants; restrictions are never relaxed.
All effective strategies are deduplicated with bounded retries.

After reproduction, draw one mutation rate uniformly from [.20,.85] and select
round(rate * eligible offspring) without replacement across both paths. Unchanged
elites/champions and fresh immigrants are excluded. Each selected offspring gets
1..5 distinct successful gene edits; requested/applied counts must reconcile.
Numeric masks, draws and lineage metadata are vectorized. Typed, bounded DAG edits
use small host loops. Birth/origin G-prefix is inherited unchanged through mutation
and crossover; actual creation generation remains separate metadata.

## Search space and continuous indicators

Family-uniform then channel-uniform sampling is retained. Compatible numeric
feature comparisons, independent rolling expressions, Boolean rolling rates/counts,
log-price transformations, typed arithmetic and compatible subtree crossover are
reachable in every lifecycle stage. Mostly pointwise priors avoid requiring costly
rolling operations in every candidate. Rules remain bounded to 32 nodes and 119
prior observed candles. Policy and minimum-age genes remain fully evolvable.

Period bank: EMA, RSI and ATR periods 2,3,5,7,9,10,12,14,15,20,26,30,45,50,60;
MACD fast 3,7,12, slow 15,26,50, signal 5,9,20. Variants append 138 channels,
preserving all original feature IDs. Values carry full certified session history
across days, by listing identity, with opening-known split adjustments. EMA seeds
at the first eligible certified bar of the frozen source sequence (July 30);
RSI/ATR seed their first complete period. Warmup is masked. No older uncertified
history is fabricated, no daily reset or finite-window EMA approximation is used.
Native fixed-period channels retain their original definitions.

`prepare_indicators` reads only certified ARTE bars and validates source physical
count/key/content hashes, universe identities, split evidence and SSD placement.
Recurrences are Numba-compiled; only top10-funnel feature rows are persisted.
Numba 0.68.0 / llvmlite 0.50.0 are preparation-only dependencies. A workstation
runtime dependency directory avoids modifying the running optimization environment.
Indicator receipts bind source definitions, predecessor receipts, compact input
hashes, values hashes, coverage and split role. Run manifests require this bank.
Preparation emits training.json and validation.json for run_search.

## Reporting and qualification

Global net-profit rank remains the overall financial table order. Validation adds
Training net profit and Training net rank; key 4 sorts by training rank. Existing
Both overall rank values are permanently based on their split's net profit and
never change when sorting. Session views label their separate session net-profit
rank explicitly; display position and selection score do not redefine rank.
session/score, net-profit and session-profit sorts remain keys 1/2/3. All table
loads/sorts run in the separate observer; pages contain at most 50 rows and the
observer maximizes its window. The training-only validation shortlist combines
half global score leaders and half session-balanced leaders before any validation
replay. Successors and training RNG are frozen first. Validation is display-only.
W&B uploads allowlisted scalar generation metrics from a separate process only.

Use short exact rule/mask and accounting comparisons plus representative timing
against V15. Synthetic timing is not evidence of complete generation speed.
Preserve checkpoints, partial generations and stop receipts when switching versions.
Commit/push source before immutable workstation deployment; artifacts live under
D:/TradingML/runtimes, never in the repository.
