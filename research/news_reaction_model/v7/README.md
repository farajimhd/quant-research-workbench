# News Reaction Model V7: point-in-time stock-state ablation

V7 tests whether a small amount of causal company and market context improves
V6. It changes one thing only: V7 adds an 85-value point-in-time stock-state
channel. V6 word TF-IDF, character TF-IDF, structured article-number features,
range heads, targets, 2019-2025 training split, 2026 validation split, loss,
scheduler, and W&B project remain unchanged.

## Input contract

The fourth channel contains raw observations available before publication:

- 18 SEC XBRL concepts, selected by `filed_at_utc < published_at_utc`;
- the clean pre-news anchor price from the reaction-label authority;
- the latest completed daily trade-bar close and volume;
- the latest short-volume observation from a session strictly before the
  New York exchange date of publication.

Each SEC concept carries a signed-log value, presence mask, filing age, and
fiscal-period code. Market observations carry raw/log-scaled values, presence,
and age. These are mechanical representation transforms, not ratios, health
scores, rankings, or other business-derived features.

The exact feature names, tag aliases, transforms, exclusions, and availability
rules are checksummed in `stock_state.py` and in the materialization manifest.
Training and live inference must use the same manifest.

V7 explicitly excludes ticker and company identity, company name, country,
sector, market cap, float, short interest, derived fundamentals, and health
scores. Country and sector currently lack a historical validity contract;
market cap and float lack adequate historical coverage; short-interest
publication time is absent. Including any of them would either leak current
state backward or invent availability.

## Architecture

```text
V6 word TF-IDF --------> weighted EmbeddingBag ----\
V6 char TF-IDF --------> weighted EmbeddingBag -----+--> gated 4-channel pooling
V6 financial numbers --> sparse+dense adapter ------+
point-in-time state ---> dense state adapter -------/
                                                     + horizon embedding
                                                     -> unchanged residual stack
                                                     -> ending/high/low range heads
```

No issuer embedding is present, so the model cannot memorize a company ID from
this channel. Missing observations remain zero with explicit presence masks;
rows are never dropped because a company lacks an SEC or short-volume value.

## Data build and training

The laptop repository is authoritative. On the workstation, from
`D:\TradingML\codes\news-reaction-model\v7`:

```powershell
python -m research.news_reaction_model.v7.run_prepare_data --execute
python -m research.news_reaction_model.v7.run_profile_sizes --real-data
python -m research.news_reaction_model.v7.run_train
```

The preparation job reads the completed V6 table month by month. Each bounded
worker resolves validity-dated SEC identity, fetches only the selected XBRL
concepts (one pre-month state plus in-month updates), and fetches a 21-day
market lookback for completed daily bars and short volume. It writes
`market_sip_compact.news_reaction_stock_state_dataset_v7` and verifies exact
population parity with V6. The default representation artifact is
`D:\market-data\prepared\news_reaction_model\v7\stock_state_v1`; the default
runtime root is `D:\TradingML\runtimes\news-reaction-model\v7`.

The training default remains `d_model=384`, four residual layers, batch 2048,
15 epochs, cosine scheduling with three restarts, and W&B project
`news-reaction-model-v3`, allowing a direct V6/V7 comparison.

Preparation defaults to 16 bounded month workers. Completed month manifests are
the resume authority, so rerunning the same command skips verified months.
Partially inserted months are safely rebuilt into the `ReplacingMergeTree` and
verified before their completion manifest advances. Ctrl+C assigns and cancels
every active ClickHouse query, cancels queued months, records an `interrupted`
status event, and returns exit code 130 without deleting completed work.

## Live use

`LiveFeatureEncoder` reuses the V6 encoder. The serving data authority must
materialize the 85-value stock state from the same point-in-time rules and pass
it explicitly. V7 rejects missing or wrong-sized state instead of silently
synthesizing a different live representation.
