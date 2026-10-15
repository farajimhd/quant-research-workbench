# V21 design discussion and decision record

Status: discussion only; implementation is not authorized yet.
Baseline inspected: V20 source at commit a55e3a139, 2026-10-15.

## Agreed decisions

| ID | Agreement | Authority |
| --- | --- | --- |
| A01 | Leave the current V20 optimization running. | User: "Let's keep that going" |
| A02 | Discuss V20 parts separately and record V21 agreements as the discussion proceeds. | User request, 2026-10-15 |
| A03 | Implement V21 only after the user's later implementation instruction. | User: "at the end ask you to implement" |
| A04 confirmed | Normalize average absolute movement as movement / close, without subtracting one; version semantics and threshold sampling. | User explicitly agreed in follow-up annotation. |
| A05 | Expand the evolvable maximum_adds integer range from 0..5 to 0..20 in V21. Update validation, initialization/mutation and normalized diversity descriptor consistently. | User: "make this 0 to 20" |

A05 does not change the $200 addition size or hard distance/risk limits. Twenty additions would permit $5000 cumulative purchases including the initial $1000 if every addition passes the existing checks; this is not a market-value ceiling. No V20 runtime changes are authorized by these V21 decisions.

This document is the design record; existing task-history files are unchanged. Later explicit decisions below supersede earlier open questions, but never authorize V20 changes or early V21 implementation.

### Agreed Phase 1 direction (latest user annotations)

- A06: Phase 1 performance selection must use score, without separate positive-trimmed-profit or minimum-position eligibility gates. Financial validity and existing hard execution risk limits remain; zero-trade candidates must be handled honestly, not excluded through a hidden performance gate. Phase 2 performance eligibility is not decided by this Phase 1 discussion.
- A07: Do not trim winning positions within a ticker for the local objective. Aggregate full net position P&L by ticker; any trimming should be across tickers. Allow strong single-ticker specialists. No complexity penalty. Exact trimming strength and remaining penalty weights await agreement.
- A08: Add a crossover route using uniformly random valid parents from the current population. Allow elite-to-elite crossover subject to five-level ancestry exclusion. Exact route budgets await agreement. Five levels means parent-child links, not five elapsed generations; elite survival does not age out ancestry.
- A09: Mutation eligibility covers every newly created next-generation candidate, including fresh random candidates, except explicitly unchanged survivors. Mutation rate and edit-count proposals remain as stated below until agreed.

### Proposed details, NOT yet approved

1. Expressions: a temporal-return template is an on-demand rule expression, not necessarily an offline feature. Use typed LAG/DIVIDE/SUBTRACT or log-price DIFFERENCE; distinguish observed-candle clocks from fixed elapsed-time horizons. Expose raw OHLC or an appropriate conversion/operator if exact raw ratios are required. No raw-price division of logarithms.
2. Initialization: use a declared hierarchical, stratified distribution over bounded rule sizes, compatible operator families, feature families/channels and lookbacks. Allocate half the numeric sampling to uniform intervals and half to log-magnitude sampling where meaningful; discrete genes get balanced choices. Include same-unit division explicitly. Report requested/accepted bins and rejection rates, because validity rejection changes the final distribution. This is broad balanced coverage, not a claim of uniformity over all strategies or an unbounded continuous space. No numerical allocation is approved yet.
3. Local objective proposal: let P_j be full fee-adjusted net P&L for ticker j, P=sum(P_j), and m the number of traded tickers. Let W be the positive-only fractional top-10%-ticker contribution removed (all losses retained). Proposed score S=P-0.25*W-0.05*D, where D is session account-equity max drawdown. Cost=-S. No trade trimming, complexity, capital-time, risk-duration or peak-risk soft penalties in this proposal; retain existing hard risk controls. Single-ticker case removes only 2.5% of its positive P&L under fractional trimming; coefficient 0.25 leaves at least 75% of any otherwise fully trimmed winner contribution before drawdown cost. Counts refer to traded tickers, not untraded universe members. Scores may be negative and still enter parent selection. A zero-trade strategy scores zero, hence may outrank losing strategies; no concealed minimum-activity gate. Fixed expert sampling and drawdown weights still require review.
4. Example reproduction budget at population 1024: 102 unchanged score elites; 205 fresh; 717 crossover children split into 359 score-tournament, 215 uniformly random valid-parent, 143 elite-pair slots. All routes honor ancestry exclusion and dedup; elites remain eligible in all routes even while an unchanged copy survives. If elite mating is impossible, try the wider valid pool and then fresh generation without relaxing ancestry. Apply the existing proposed 20%..85% mutation draw and 1..5 edits to all 922 non-survivors, including fresh generation and fallback candidates. These counts/rates are proposals.
5. Expert archive proposal: 64 slots from exact-deduplicated positive-net-profit candidates, ranked by raw session net profit: 32 from percentile 90..100, 16 from 70..90, 10 from 40..70, 6 from 0..40. Percentiles defined by deterministic ranked candidate IDs to split ties consistently. Uniform sampling without replacement within bands; highest-profit candidate guaranteed inside the top band's allocation. Underfilled bands donate slots to the highest-profit remaining positive candidates; fewer than 64 positives yields a smaller archive. This is an explicit proposed archive gate, distinct from score-only breeding; no positive-score requirement. It favors discovery coverage, not proven trading-behavior diversity. Keep a bounded reproducible historical archive/reservoir rather than claiming to retain all prior candidates; retention policy still to finalize. Phase 2 expert/fresh allocation is not revised yet.

Net-profit-only archiving and profit-band sampling are alternatives discussed by the user; the stratified option above is the assistant's recommendation, not an accepted decision. Distinct candidate hashes or profit bands do not guarantee different trading behavior.

### Follow-up: feature semantics and action limits

- A04: User conditionally requested correcting average-movement normalization if no rationale was found. Inspection traced `movement / close - 1` to the blanket price-history normalization introduced in V7 commit 56b9706c8. The only explicit later rationale is compatibility with persisted authority (V20 search_space.py); no independent magnitude-normalization rationale was found. Record the V21 correction as `average absolute movement / close`, with an explicit new semantic identity and corresponding threshold priors. Implement only at the later V21 implementation stage; do not reinterpret V20 feature IDs, certificates or results. This shift loses no mathematical information in the old representation but makes the new representation meaningful around zero.
- Swing right is a confirmation window after the pivot, not future information available at the pivot. history_bank.calculate requires the full left/pivot/right window and then exposes only confirmation state through the previous clock. A pivot at t with right=3 cannot be used at t; availability is after confirmation, with the producer's additional previous-clock shift. Preserve this causal rule.
- Existing enriched return channels are close-to-close log returns, `log(close_t / close_(t-h))`; they are not a set of OHLC-to-previous-OHLC returns. Completed-candle body/range/location are separate. No additional return channels agreed yet.
- One position per candidate/listing at a time; no explicit per-session entry-count cap. After full closure later re-entry is possible. Add count is 0..5 per position, reset on new entry, not replenished by partial reductions. Reduction/trail counts have no analogous explicit numeric cap; signal, holding, cooldown/profit/age rules apply as relevant. The trail high-water is updated when the trail rule fires, not an unconditional maximum of every intervening price.
- Addition size remains $200 and requires the evolved add rule, minimum profit, age, cooldown, remaining add count and prospective whole-position risk checks. Five additions permit $2000 cumulative buys including the initial $1000 within one position; this is not a market-value ceiling.
- Entry checks are separate: stop distance `(fill-stop)/fill <= 0.20`; planned loss `quantity*(fill-stop) + modeled costs <= $300`. Thus a $1000 entry has at most $200 price-to-stop risk plus fees, with the $300 cap still checked. Additions retest the entire resulting position, not a fresh $300 allowance for each add. Invalid requests are rejected, not automatically resized.

## Big picture: current V20 baseline, not V21 decisions

Certified causal inputs -> selected features and typed expressions -> five lifecycle signals -> position policy and fixed execution/risk checks -> financial results -> objective plus eligibility -> reproduction/archive.

Phase 1 evolves session experts independently. Phase 2 evolves candidates globally across training sessions. Both use the same feature catalog and genome representation. Validation is diagnostic and does not feed reproduction.

There are 957 selectable feature channels. A candidate uses only channels referenced by its rules; 957 is not a per-candidate parameter count. Indicator periods and historical windows are discrete channels, not continuously fitted indicator coefficients.

## 1. Input features

| Family | Channels | Contents |
| --- | ---: | --- |
| Original scalar bank | 37 | Log OHLC; bar/session VWAP relative values and validity; log volume/trades and trailing 60-second totals; prior-session relative volume and availability; MACD line/signal, RSI14, ATR14, EMA7/26; time-of-day sin/cos; premarket/regular/after-hours flags; inter-candle gap; float/shares and availability; days since split/reverse split and availability; price/extreme validity. |
| Structural levels | 110 | Five below and five above, each with center/lower/upper relative distance, observation counts (all/today), confirmation age, support/resistance/transition flags, historical-origin flag and presence. |
| Session split flags | 2 | Split this session; reverse split this session. |
| Raw causal history | 332 | Recent high at 1..60; lagged close at 1..60 (named momentum_close); mean dollar activity at 1..60; average absolute close movement at 2..32; 121 confirmed swing-low left/right pairs. |
| Relative causal history | 272 | Price-valued history divided by current close, minus one. Attention/dollar activity does not have this price-relative variant. In particular, average_move.relative_to_close is movement/close - 1, not movement/close. |
| Market/execution | 11 | Bid/ask relative to close, spread fraction, quote age, log execution volume/notional/trade count, raw bid/ask/spread/close. |
| Indicator variants | 138 | EMA and ATR raw and relative, RSI; relative MACD line/signal/histogram variants, as detailed below. |
| Own-stock enriched features | 32 | Returns/volatility at 5/15/60/300 seconds; volume/trade acceleration; volatility-to-fee ratio; seven completed-candle fields at 5/15/60 seconds. |
| Broad-market context and rank | 23 | Seven context fields at 15/60/300 seconds, plus trailing-60-second dollar-volume percentile and its 15-second change. |
| **Total** | **957** | |

Indicator period bank: 2, 3, 5, 7, 9, 10, 12, 14, 15, 20, 26, 30, 45, 50, 60. EMA/ATR each have raw and relative channels; RSI is normalized. MACD fast choices 3/7/12, slow 15/26/50, signal 5/9/20. These produce 75 EMA/RSI/ATR plus 63 MACD channels.

Completed-candle fields: body/close, range/close, close location inside range, log return, log volume, log trades, active fraction. Volatility is the square root of summed squared one-second log returns, with missing coverage masked. Acceleration compares short-window and longer-window activity rates. The fee-ratio channel uses the producer's fixed order/fee assumptions.

Context fields per horizon: median return, mean return, advancing fraction, return dispersion, coverage fraction, own return minus median, own return percentile. Dollar-volume rank is a percentile (highest = 1, lowest = 0, average ties), not ordinal rank 1/2/3. Its change is a percentile difference over 15 seconds.

Scope: enriched stock features are prepared only for listings entering the top 10, with causal history. Broad-market peers supply the minimal aggregates/rank census; not full per-peer candle feature banks. Membership gates new entries; an already-held position still needs management after leaving the top 10. A session can contain more than ten distinct eligible listings over time.

Validity: unavailable/insufficient-history values have masks; they are not made into fabricated zero observations. Features and decisions are causal; confirmed swings must wait for right-side confirmation. Enriched horizons are elapsed seconds. Program temporal operators count observed candles; these are not interchangeable clocks.

## 2. What the GA evolves

Five independent effective rule outputs: entry, exit, add, reduce, trail. Entry applies while flat; the other rules apply while holding. Internal open_rules/rules storage does not mean ten independent effective trading decisions.

For each rule the GA can change feature IDs, numeric/Boolean constants, operator types, operand links, subtree structure, temporal windows and compatible subtree crossovers. It also evolves the policy and holding-age parameters below. Feature substitution is restricted to compatible units/semantic classes; structure replacement can introduce other valid features.

| Operation family | Available operations |
| --- | --- |
| Arithmetic | Add, subtract, multiply, divide, absolute value |
| Comparisons | Greater, greater-or-equal, less, less-or-equal, cross above, cross below |
| Logic | AND, OR, NOT; Boolean-to-ratio conversion |
| Temporal | Lag, mean, minimum, maximum, difference, standard deviation |

Program windows: 1, 2, 3, 5, 10, 20, 30, 60, 120 observed candles. Each rule has at most 32 nodes. Combined temporal extent must fit 120 observed candles (119 preceding observations); lag/difference 120 is consequently invalid. Output must be Boolean. Cycles, incompatible units, self-comparisons and prohibited implicit constant signals are rejected. Explicit disabled management rules are allowed. Mutation edits 1..5 distinct effective fields with bounded retries; ineffective edits are rejected.

### Numeric thresholds: sampling is not a hard execution range

Feature metadata defines initialization ranges: e.g. price typically 0..1000, price-relative history -0.5..0.5, bid/ask relative -0.1..0.1, spread fraction 0..0.1, quote age 0..3600, normalized RSI 0..1; exact ranges depend on channel. Positive/signed log-scale priors favor small thresholds in several families. Numeric literal mutation adds Gaussian noise with scale max(1e-6, 5% of absolute value). It does not clamp to the initialization range. Program validation requires finite, correctly typed literals, not those feature sampling limits. Input values are not clipped to threshold priors.

Feature sampling first chooses an eligible family, then a channel, to avoid large channel families dominating initialization purely by size.

## 3. Evolvable position policy

| Gene | Search domain | Meaning / constraints |
| --- | --- | --- |
| Stop mode | Fixed fraction or confirmed swing low | Chooses the initial stop source. |
| stop_fraction | 0.0001..0.20 (0.01%..20%) sampled | Initial stop = entry price * (1-fraction) in fixed mode. Inactive in swing mode. Policy validation alone accepts up to 1, but execution rejects distances over 20%. |
| swing_left / swing_right | Each 1,2,3,4,5,10,15,20,30,45,60; (0,0) disables | 121 confirmed pairs. Stop must be finite, positive, below entry, within distance and dollar-risk bounds. |
| target_fraction | 0.0001..1 (0.01%..100%); includes exact 1 | Initial target = entry * (1+fraction). Hitting target triggers a reduction, not necessarily a full exit. A filled target advances the next target by average cost * fraction. |
| reduce_fraction | 0.0001..1 | Fraction of current quantity sold on reduction/target; 1 closes the whole position. |
| trail_fraction | 0.0001..1 | Trail rule ratchets stop to max(old stop, high-water * (1-fraction)); never loosens it. |
| add_minimum_profit | 0 or sampled 0.0001..1 | Minimum price return versus average cost before signal-driven add; not a net-after-fee profit threshold. |
| reduce_minimum_profit | 0 or sampled 0.0001..1 | Minimum price return for signal-driven reduction; target-driven reductions have a separate trigger. |
| maximum_adds | Integer 0..5 | Maximum additions in a position. |
| cooldown | Integer 1..300 seconds | Minimum elapsed time since entry/add/reduction before another add/reduction. Not a general re-entry cooldown or an exit delay. |
| Minimum holding ages | Integer 0..300 seconds per exit/add/reduce/trail rule | Gates those rule signals; entry has no holding age. Does not disable hard stop/session-end execution. |

Fractions are proportions, not percentages: 0.03 means 3%. Non-stop positive fraction sampling favors small values but allows values through 1. Numeric policy genes are redrawn within their sampling domains on mutation; unlike expression thresholds they do not use unconstrained Gaussian mutation.

## 4. Fixed execution and constraints (not GA genes)

- Long positions; fixed $1000 entry and $200 per addition. Quantities are dollar size / execution price in this research simulator.
- Current campaign fees: $0.005/share, minimum $1/order; extra cost_bps = 0. These are execution assumptions, not learned parameters.
- Maximum planned stock-position risk $300; maximum stop distance 20%. Entry/add requests violating these are rejected. Planned risk includes modeled costs; it is not a guarantee against a gap through the stop.
- Entry requires causal top-10 membership and usable execution evidence. Management continues for held listings; hard stops and session-end liquidation coexist with evolved rule signals.
- Stop/exit has precedence over reduction, which has precedence over adding. Target-driven reduction and rule-driven reduction share reduce_fraction.
- No evolved total-account cash budget, portfolio risk budget, short-side gene, order-type gene or per-candidate dollar sizing in the inspected policy/execution model. Per-stock planned risk must not be described as a portfolio loss cap.
- Position state (quantity, average cost, stop, target, etc.) is maintained by execution. It is not automatically a selectable feature channel; age/profit gates are explicit policy mechanisms.

## 5. Separate performance eligibility from cost

Current V20 Phase 1: positive trade-trimmed profit, >=3 completed positions, financial/risk validity. Phase 2: positive net profit AND positive session-trimmed profit, >=20 positions, >=8 active sessions, financial/risk validity. These are hard performance gates in addition to score, not merely score penalties. Validation observes a training-selected eligible shortlist; raw-profit display rank is not parent eligibility or objective rank.

V21 selection, activity thresholds, trimming, risk limits, feature additions/removals and ranges remain open. Do not infer approval from this inventory.

## Discussion queue

### Follow-up: temporal expressions and Phase 1 selection audit

- The evaluator supports LAG plus DIVIDE, so x / LAG(x, h) is representable for suitable numeric inputs. Raw market.close is available; open/high/low are exposed as logs. DIFFERENCE(log_high, h), and similarly log_open/log_low/log_close, is an evolvable log-return expression. These temporal operations use observed candles, not fixed elapsed seconds. Dividing log prices is not a price return; there is no EXP operator to recover raw OHLC ratios from log differences.
- Representable is not the same as directly sampled: the current numeric expression generator uses a ratio-valued denominator for division, rather than explicitly generating same-unit raw-price/raw-price ratios. Ratio-valued features can participate in temporal ratios, and log-price differences are directly sampled. Whether V21 should add an explicit ratio-change operator/template or additional elapsed-clock OHLC return channels remains open.
- Phase 1 score: T - 0.10*D - 0.01*R - 0.0004*C - 0.05*N*L - 0.50*P. T is positive-only top-10%-position-trimmed net profit; D is session drawdown; R planned-stop-risk dollar-hours; C capital dollar-hours; N completed positions; L nonnegative loss magnitude of the mean worst 10% of completed-position P&Ls (fractional boundary); P peak per-stock planned position risk. Other-session loss is zero for this single-session stage. Cost is negative score; higher score is better. No separate session trim, win-rate reward or active complexity penalty in this local score.
- Local parent/archive eligibility: T>0, N>=3, valid terminal accounting and risk bounds. Score need not be positive. All sessions evolve independently from 1024 fresh candidates for 30 generations in the current campaign; no validation feedback.
- Each next local population nominally has up to 102 unique eligible score elites, 205 unique fresh candidates, and 717 crossover children when elite capacity is filled. Missing elites or failed unrelated-parent mating increase fresh/crossover allocation as specified by lineage.next_population; failed mating uses fresh candidates, never weaker eligibility. Parents use best-of-three rank tournaments with replacement among eligible candidates; second parent must not share tracked ancestry within five parent-child links. Up to eight mating attempts. No cross-session mating in Phase 1 despite reuse of the selector's internal global route.
- Each generation draws a mutation rate uniformly from 20%..85% of crossover children; rounded count is selected without replacement. Each selected child gets 1..5 distinct effective edits. Elites and fresh candidates are not in that mutation pass. Effective duplicates are rejected.
- Archive selection is separate from breeding: merge previous archive with eligible current candidates, canonical dedup, best 256 by score, retain up to 64. If more than 64, start with best score and greedily maximize 70% normalized score + 30% normalized squared parameter distance to the nearest selected member. This is parameter diversity, not trade-behavior diversity. Only retained archive, not every historical eligible candidate, carries across archive updates. At Phase 2 initialization, up to 768 unique experts are drawn round-robin across sessions and at least 256 fresh candidates fill 1024; shortages become fresh candidates.

The above selection/cost details are audited V20 behavior, not newly approved V21 choices.

1. Feature families, clocks, availability and rank representation.
2. Expression operations, complexity and threshold bounds.
3. Position policy, sizing, target/partial-exit semantics and risk limits.
4. Local/global costs versus hard eligibility and archive selection.
5. Runner, caching, profiling, validation and user-interface requirements.
6. Final consolidated approval, then implementation.

## Source references

- V20: features.py, enriched_math.py, prepare_enriched.py, search_space.py, genome.py, program.py, expressions.py, variation.py, semantics.py, evaluator.py, protected_transition.py, run_train.py, objectives.py, README.md.
- Inherited catalog: ../v7/features.py and ../v6/torch_backtest/feature_bank.py, history_bank.py.
- Catalog count verified by importing V20 features with bytecode writing disabled: 957 total = 764 inherited + 138 indicator variants + 55 enriched.
