# Long price-action experiment v1

Research → **Price-action experiment** displays NVDA, 2026-07-31, the full
09:30–16:00 ET session. This isolated product is not used by the saved teacher
run and does not modify its certified labels. There is no fill model, fee,
slippage, liquidity constraint, sizing, stop execution or target execution.

The source is the SHA-verified V6 packed bank, decoded from float32 log OHLC
and saved 1s MACD. Of 23,400 activity slots, 22,748 have valid prices/extremes;
652 remain unlabelled, without forward filling. Approximate source volume is
105,636,231. Default discount half-life is 30 elapsed seconds; stop offset is
0.01 price units. The adapter currently supports `tf=1s` only.

MACD >= signal defines long, otherwise short, including equality in long.
Successive observed usable indicator samples define sign runs. Each long run
pairs with its previous short run; a first long run has no preceding short.
Missing-price seconds receive no artificial candles or labels.

Pair references use minimum open/close over S+L, maximum open/close over L,
start minus offset for stop, and maximum high over L for target. Their difference
is a reference range, not necessarily a chronologically achievable trade.
These references do not constrain this initial action-value algorithm.

For each valid close, work backward through the session. ENTRY considers every
later close j: discount(now,j) × [close(j) − close(now) + discounted best flat
value after j]. WAIT carries the next candle's best flat value. EXIT includes
the current trade's price change once and adds discounted flat continuation;
HOLD discounts the best later exit. Choose the greater value; exact ties prefer
WAIT/HOLD, and the last candle cannot enter. The forward sequence begins flat
and always closes an existing position by session end. This is long-only;
either MACD direction can contain an ENTRY or EXIT, and a pair may contain
multiple trades. MACD geometry supplies visual context, not an entry gate.

Arrays are O(N) memory with O(N²) future-exit comparisons, bounded to this one
RTH ticker. The generated run took about five seconds after source access.
It contains 1,779 MACD episodes, 889 S→L pairs and 6,019 closed price-action
trades. Sum of undiscounted price changes is 231.042982; session-start discounted
value is 1.488468. These distinct quantities are hindsight price-action outputs.

The chart shows one half-size marker per valid candle. ENTRY is an up arrow
below, EXIT a red down arrow above; numbers show the chosen discounted action
value in price units. WAIT has no text. HOLD numbers are optional. The candle
inspector gives all flat alternatives and the actual sequence's held context,
entry basis, realized price change and future continuation. Flat and held
values are conditional alternatives, not a four-way action choice.

Both Research paths shade **MACD sign episodes**, green for >= signal, red for
< signal. Original teacher probability values remain unchanged. Both paths
retain their mounted chart across sidebar/path navigation; container geometry
and closed state use separate Research-only storage keys.

Reproduce or verify the default immutable product:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B research/rl_trading/v6/run_price_action_labels.py
```

Artifacts are under
`D:/TradingML/runtimes/rl-v6-price-action-long-v1/NVDA/2026-07-31-r2`.
The first preliminary product remains intact in the neighboring original-date
directory. r2 aligns episode ends with the next observed sign-change boundary;
the labels and price-action sequence are unchanged. A different half-life or
offset requires a new `--output-dir`. That output does not change the pinned UI.

Review the real experiment through the managed frontend:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/run_frontend.py ui:review -- --research-price-action --output-dir D:/TradingML/runtimes/rl-v6-price-action-ui-review
```
