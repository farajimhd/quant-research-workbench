# News Reaction Model V18

V18 replaces fixed independent horizons with causal, single-ticker news
episodes for low-priced stocks. It is a redesign of the learning example, not
an incremental V16 market-attention experiment.

## Objective

At each eligible single-ticker article, predict the observable response from
that publication until the next ticker-linked news item or the episode expiry:

- direction: neutral, upside, or downside;
- path: no move, sustained, spike-fade, flush-recovery, reversal, or mixed;
- flow: balanced, demand-dominant, or supply-dominant;
- actual high, low, and terminal returns in percentage points.

The model does not claim who bought or sold. `Supply-dominant` means only that
eligible trades were observably seller-dominant under the causal quote test.

## Episode contract

The model-input universe is single-ticker news. A new episode may start only
when all of these conditions hold:

1. the article is company-specific, regulatory, editorial, or an independent
   analyst action;
2. it has the exact V15/OpenAI, point-in-time stock-state, and time inputs;
3. V15's causal pre-publication anchor feature is positive and within the 1%
   planning margin around `$20`, and the exact ordered-SIP anchor is strictly
   below `$20` before the episode is admitted to the completed dataset.

Once an episode starts, later single-ticker follow-ups remain eligible even if
the stock moves above `$20`. The root price, not the later price, defines the
experiment universe.

An episode expires at 20:00 New York on the second subsequent exchange session
after its last material node. Weekends and exchange holidays therefore do not
consume the inactivity clock.

Material updates extend that clock. Duplicates, recap/movers/why-moving items,
and analyst commentary reacting to a recent non-analyst catalyst attach as
internal context without extending it. A materially independent catalyst
closes the active episode and may start another episode if its own root price
passes the filter.

Multi-ticker articles are never model inputs or episode nodes. They are causal
censors: if one mentions the ticker, the preceding response interval ends at
its timestamp so movement after that new information is not attributed to the
earlier single-ticker article.

The role taxonomy is:

- `root`
- `material_update`
- `analysis`
- `reactive`
- `duplicate`

The root families are `company`, `regulatory`, `editorial`, `analyst`, and
`other`. `Other` is never root-eligible.

## Inputs and non-redundant storage

V18 opens the completed V15 arrays read-only. It does not duplicate:

- 3,072-value OpenAI embeddings;
- point-in-time stock state;
- the 11-value V15 exchange-time vector.

Its compact sidecar stores source indices, episode roles, episode position and
age, up to eight earlier modeled nodes from the same episode, response bounds,
and exact target evidence. Prior interval response values enter context only
after that interval has ended. Missing prior responses are zero with an
explicit validity bit.

Downloaded single-ticker articles without V15 inputs still participate in
episode boundaries, materiality, expiry, and the unembedded-node count. They
are not silently turned into zero embeddings and are not supervised rows.
The manifest reports their count.

V16 market-wide news, leader, and attention channels are deliberately absent.

## Target authority

V18 has no dependency on the old fixed-horizon reaction-label authority or
the V16/V17 certified-target sidecar. V15's signed-log anchor feature is
inverted only as a cheap planning filter. Because that feature is
float32-compressed, planning admits a 1% boundary margin; this cannot make a
row trainable. It is never retained as target truth.

For each article interval V18 reads compact SIP events in exact
`(sip_timestamp_us, ordinal)` order and applies the shared canonical trade
condition and causal quote-ASOF contract. The reader includes the immediately
preceding exchange session when necessary and selects the last `update_last`
trade strictly before publication as the exact anchor. The same ordered event
batch supplies the interval high, low, terminal, path, and flow evidence.
Corporate-action crossings are masked. The target interval is
`[published_at_utc, boundary_utc)`.

The completed audit requires every populated target's stored anchor to equal
the anchor embedded in its raw target metrics. Episodes whose exact root
anchor is missing, non-positive, or at least `$20` are removed as a whole.
The manifest also records differences between the V15 planning approximation
and exact SIP anchors.

Workers own bounded ticker groups. Each worker fetches one exchange session
once for all required tickers in its group, evaluates every interval sharing
that evidence, durably checkpoints the completed batch, and releases old
sessions. Defaults are 16 workers, 64 tickers per query, and two ClickHouse
threads per query. These are separate controls because more Python workers do
not justify an unbounded ClickHouse query.

The meaningful-move threshold is the larger of 0.1% and the 35th percentile of
training-only interval excursion magnitude. It is frozen before 2026 classes
are materialized.

Return regression targets are percentage points:

```text
100 * high_return
100 * low_return
100 * terminal_return
```

Smooth-L1 loss limits the effect of genuine low-price heavy tails. The three
classification losses and the weighted regression loss are averaged.

## Build

V18 requires only the completed V15 representation and the existing compact
SIP events, condition reference, exchange calendar, and split table. Do not
run the old V16/V17 authority or fixed-horizon builders for V18.

```powershell
python -m research.news_reaction_model.v18.run_prepare_data --execute
```

The launcher defaults to:

```text
--workers 16 --tickers-per-query 64
```

The target phase is safely resumable after Ctrl+C. Because this is the V18.2
target contract, any older partial V18 state fails closed; use `--restart` once
to discard an incompatible V18.1 sidecar.

To use more workstation concurrency:

```powershell
python -m research.news_reaction_model.v18.run_prepare_data --workers 24 --tickers-per-query 64 --execute
```

Start with 16. Increase to 24 only if ClickHouse CPU, memory, and disk are not
saturated; 24 workers issue up to 48 ClickHouse execution threads.

## Profile, train, evaluate

```powershell
python -m research.news_reaction_model.v18.run_profile_sizes
python -m research.news_reaction_model.v18.run_train
python -m research.news_reaction_model.v18.run_evaluate --checkpoint <best_val.pt>
```

Defaults remain comparable to the established experiments: `d_model=384`,
four residual blocks, six-head episode attention, batch 2,048, 50 epochs, and
an epoch-local cosine schedule whose peak decays by 0.98. W&B uses the existing
`news-reaction-model-v3` project.

Evaluation reports classification accuracy, balanced accuracy and macro-F1;
high/low/terminal MAE and RMSE in percentage points; and descriptive terminal
one-share P&L overall, long/short, node role, and root family.

## Limitations

- V18 can only model articles with the completed V15 representation. Missing
  articles remain causal boundaries/context metadata but not text tokens.
- Deterministic episode linkage is versioned and auditable, but semantic
  ambiguity remains. The episode manifest and role/family breakdowns must be
  reviewed before interpreting model quality.
- Terminal one-share P&L is descriptive. It ignores costs, fills, overlap,
  capital constraints, and execution policy.
