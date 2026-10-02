# Long swing-opportunity labels v2

Research → **Price-action experiment** now displays pair-local opportunity
quality for NVDA, 2026-07-31, 09:30–16:00 ET. This separate product is not used
by the saved teacher run. Original teacher targets and V1 artifacts are intact.
There are no fills, fees, slippage or sizing; stop/target remain references.

The source is the SHA-verified V1 decoded candle product, whose consumed bank
clock/scalar hashes and bank certificate are retained in the V2 proof. V1
classification/value fields are never used: the adapter selects only clock,
OHLC and 1s MACD columns. There are 22,748 valid-price candles and 652 invalid
price/extreme activity rows that remain unlabelled, without forward filling.

## Local opportunity scores

MACD >= signal is long, otherwise short. Each long episode pairs with its
preceding short episode, or stands alone if the session starts long. Pair
geometry uses minimum open/close over S+L, maximum open/close over L, start
minus offset for stop, and maximum high over L for target. Geometry remains
descriptive; action values use closes and enforce chronological order.

For every candle i in a pair:

- Entry gain = max positive `(close[j] - close[i]) * 2^(-elapsed_seconds/30)`
  over strictly later candles j in this pair's L episode; zero if none qualify.
- Entry quality = entry gain / the pair's largest entry gain, or zero if the
  pair has no positive gain.
- Reference entry = earliest candle with the largest discounted entry gain.
  Reference exit = earliest highest strictly subsequent L close. The best
  discounted exit can differ from the highest close; both clocks are recorded.
- Exit gain = current L close minus reference entry close, only after entry.
  Exit quality = clip(exit gain / best subsequent L gain, 0, 1).

The default threshold is 90%; the UI also offers 80%, 95% and 100%. Qualifying
ENTRY/EXIT candles are **alternative opportunities**, not repeated executions.
Each profitable pair has exactly one chronological reference entry/exit pair.
Carry compares the next best opportunity with the local opportunity using
elapsed-time discount at a common clock; it is not added to any quality score.

Blue HOLD dots indicate the reference-held interval, gray WAIT dots are outside
it. HOLD's optional number is 1 - exit quality (1 when no eligible L exit yet);
WAIT has no text. These quality/complement scores are not probabilities.

The combined view shows entry and exit opportunities over the reference
context. If both conditional alternatives qualify at one clock it shows EXIT
and exposes the conflict count. ENTRY/WAIT and EXIT/HOLD views preserve each
alternative independently. The reference view shows only the single selected
pair and its intervening HOLD candles. All views retain one marker per candle.
MACD sign shading remains green/red in both Research paths.

## Saved full-session result

At 90%: 889 S→L pairs, 888 positive reference trades, 2,258 entry-opportunity
candles and 2,108 exit-opportunity candles. 514 pairs have several qualifying
entries; 498 have several exits. No candle qualifies for both at this threshold.
The reference ledger's summed undiscounted price change is 122.788402.
All displayed quality scores are bounded by 0 and 1. Reference positions do not
overlap, and all exits follow their entries. V1's 6,019-trade result remains a
separate historical experiment, not the current chart authority.

Reproduce or verify the pinned product:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v6/run_price_action_opportunities.py
```

Artifacts: `D:/TradingML/runtimes/rl-v6-price-action-long-v2/NVDA/2026-07-31-r2`.
The provisional V2 directory remains intact. r2 discounts carry to a common
pair-start clock; local qualities and selected reference positions are identical.
Producer parameters require a new `--output-dir`; the chart threshold and view
classify the saved scores on demand without rewriting the product. The source
adapter currently accepts tf=1s only.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/run_frontend.py ui:review -- --research-price-action --output-dir D:/TradingML/runtimes/rl-v6-opportunity-ui-review
```

Research retains both mounted paths through sidebar/path navigation. Container
geometry and closed state persist in independent Research-only storage keys.
V1 can still be reproduced with `run_price_action_labels.py`; no teacher or PPO
training is launched by these scripts or read-only API endpoints.
