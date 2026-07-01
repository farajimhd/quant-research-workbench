# Temporal Event Model v3 Trainer And Model Design

This guide defines the intended v3 trainer/model design before implementation.
It is a verification target: implementation should not start until the design is
approved.

## Goal

`temporal_event_model/v3` trains a multimodal temporal prediction model on the
ticker-month rolling cache. The model consumes raw event context, daily bar
context, precomputed Qwen text embeddings, XBRL context, and future-label
tensors. It should train end-to-end, but production inference should be able to
cache encoder outputs and run only the cheaper fusion/head path when context has
not changed.

The trainer must be stateful. A checkpoint must be sufficient to resume from
the same data position and reproduce benchmark batches across model variants.

## Data Source

The primary data source is the ticker-month cache loader:

```text
research.mlops.rolling_loader.ticker_month_dataset.AsyncTickerMonthBatchLoader
```

The cache is produced by:

```text
research.mlops.rolling_loader.run_build_ticker_month_cache
```

Current builder contract:

- Work is split by month and ticker. Each ticker/month package is independently
  reusable and contains raw compact events, eligible origins, event-window
  indexes, pivoted intraday labels, daily bars, text embeddings, and XBRL rows.
- The builder checks `training_category_reference` at startup. If the table is
  missing or empty it builds stable category ids before cache construction; a
  force flag can rebuild/append missing categories. Category ids are persisted
  and reused across periods so model embedding ids stay stable.
- Text context is read from precomputed embedding tables:
  `news_text_embeddings` and `sec_filing_text_embeddings`. The builder does not
  tokenize text or run Qwen during ticker/month cache construction.
- XBRL category ids are joined at build time and written into `xbrl.parquet`.
  Id `0` is reserved for missing/unknown values.
- All absolute cached time features use UTC. The New York session conversion is
  only used to decide active intraday origin eligibility.
- Event history is cached as raw rows. The default cache stores lookback rows
  before each physical part start, not before every origin. The loader may
  request any event coverage that fits inside the cached lookback.
- Intraday labels are stored as compact/pivoted `next_*` label arrays, one row
  per saved eligible origin. Builder output must satisfy:

```text
origins_part_N rows == event_window_index_part_N rows == intraday_forward_labels_part_N rows
```

  Candidate labels for origins later rejected by the event-window eligibility
  pass are filtered before write. The part manifest records
  `labels_filtered_out`; this should match the skipped invalid origins for that
  part. Missing labels for eligible origins, duplicate compact label rows, or
  label identity disagreements are fatal build errors.

The loader should support deterministic and non-deterministic modes:

- deterministic benchmark mode: fixed `dataset_id`, seed, hash buckets, and
  sample limit produce the same batches across runs
- production training mode: stochastic shuffling can vary by run while still
  being checkpointable

The loader should emit all selected data groups but allow low-level selection
for pretraining or ablation, such as events-only, events+labels, text-only, or
full multimodal training.

Current loader contract:

- It builds a package/sample plan from completed ticker/month manifests.
- It can filter origins by a requested training/validation period.
- It can shuffle at the package level and within loaded package groups while
  remaining reproducible from `dataset_id`, seed, split, hash buckets, and
  checkpointed loader state.
- It materializes all selected origins from a loaded package group. For the
  normal training path it uses sliding raw event streams ending at each origin,
  not byte-encoded event chunks.
- It validates event streams as ordinal-contiguous and ending at the requested
  origin. Invalid cache files should fail fast instead of silently dropping
  samples.
- Text and XBRL contexts are selected as-of `origin_timestamp_us`, using the
  latest available rows/items first and zero-filling only when insufficient
  historical context exists.
- Label materialization first uses the part's aligned label rows; if an older
  cache is not row-aligned, it builds a strict origin-to-label map and gathers
  only matching rows.
- The loader exposes `state_dict()` and `load_state_dict()` so training
  checkpoints can resume at the same package position and origin cursor.

## Batch Contract

Each batch keeps identity fields for audit and checkpoint logs:

```text
ticker                  [B]
origin_ordinal          [B]
origin_timestamp_us     [B]
source_part_key         [B]
```

Event inputs:

```text
raw_event_stream        [B, 1024, F]
raw_event_mask          [B, 1024]
raw_event_feature_names tuple[str, ...]
```

The event stream is a sliding window ending at the origin. It is not byte-encoded
by default. Event fields should include only useful model inputs, with redundant
identity columns suppressible by loader args. Identity columns remain available
in metadata for audit.

The default event output mode is `raw_stream`. Encoded event chunks remain a
legacy/optional path and should not be used for v3 training unless an ablation
explicitly asks for them.

Daily bar inputs:

```text
ticker_daily_bars       [B, ticker_offsets, bar_features]
ticker_daily_bar_mask   [B, ticker_offsets]
ticker_daily_bar_time_features [B, ticker_offsets, bar_time_features]
global_daily_bars       [B, global_symbols, global_offsets, bar_features]
global_daily_bar_mask   [B, global_symbols, global_offsets]
global_daily_bar_time_features [B, global_symbols, global_offsets, bar_time_features]
```

Text embedding inputs should use precomputed Qwen embeddings, not token ids, for
v3 training:

```text
ticker_news_embeddings  [B, 8, 2, 1024]
ticker_news_item_mask   [B, 8]
ticker_news_chunk_mask  [B, 8, 2]
ticker_news_item_time_features [B, 8, text_time_features]

market_news_embeddings  [B, 16, 2, 1024]
market_news_item_mask   [B, 16]
market_news_chunk_mask  [B, 16, 2]
market_news_item_time_features [B, 16, text_time_features]

sec_filing_embeddings   [B, 4, 8, 1024]
sec_filing_item_mask    [B, 4]
sec_filing_chunk_mask   [B, 4, 8]
sec_filing_item_time_features [B, 4, text_time_features]
```

The stored embedding source is:

```text
news_text_embeddings
sec_filing_text_embeddings
```

Embeddings are written as `Array(Float32)` in ClickHouse. Loader/model code may
cast them to bf16 on GPU during training. Missing items/chunks are zero-filled
and masked false.

Default context capacities for v3 are:

```text
ticker news:   latest 8 items, 2 chunks per item
market news:   latest 16 global/news items, 2 chunks per item
SEC filings:   latest 4 filings, 8 chunks per filing
XBRL rows:      latest 4096 rows
```

The builder may cache a slightly larger historical context envelope so loader
experiments can reduce these limits without rebuilding the cache. The model only
sees the loader-selected as-of subset for each origin.

XBRL inputs:

```text
xbrl_value              [B, 4096]
xbrl_mask               [B, 4096]
xbrl_time_features      [B, 4096, xbrl_time_features]
xbrl_period_end_time_features [B, 4096, xbrl_period_time_features]
xbrl_category_ids       field-specific [B, 4096]
xbrl_confidence         [B, 4096]
```

XBRL time is split into two channels:

```text
availability time: source timestamp_us, used for as-of/no-lookahead selection
period time:       period_end_date/fiscal period, describes the accounting period
```

Labels:

```text
future_intraday_bars      [B, H, label_features]
future_intraday_bar_mask  [B, H]
intraday_labels           dict[str, [B, H]]
corporate_action_labels   dict[str, [B, D]]
```

Primary price targets should use bid and ask fields. Redundant mid-price targets
should not be trained by default unless explicitly enabled for an ablation.

Intraday labels are future-only and session bounded. They are computed from
events on the same New York trading date as the origin and do not cross the
20:00 ET session end. Each unavailable horizon is masked out rather than filled
as a valid target.

Corporate-action labels are daily future labels, not intraday bars. They match
the forward daily price-label horizons through `plus_28d`: `+1d,+2d,+3d,+7d,+28d`.
They include split, reverse split, forward split, dividend ex-date, special
dividend ex-date, and any-corporate-action flags. These labels are computed from effective dates.
Corporate-action inputs are separate X/context features selected by
availability time so declared future dividends can be seen only after they are
available, while labels still forecast future effective events.

## Atomic Model I/O Representation

The loader keeps source-aligned tensors for audit. The v3 model should not feed
every raw field exactly as stored. A versioned model data adapter should convert
loader tensors into the atomic model inputs below, while preserving identity
fields outside the model for audit and checkpoint logs.

### Atomic Model Inputs

| Model input atom | Loader source | Shape before embedding/projection | Required representation | Encoder path |
| --- | --- | --- | --- | --- |
| `event_type_id` | `bitAnd(event_meta, 1)` | `[B, L]` | categorical id, `0=quote`, `1=trade` | event categorical embedding |
| `event_primary_price_scale_id` | `bitAnd(event_meta, 2) != 0` | `[B, L]` | categorical id, `0=/100`, `1=/10000` | event categorical embedding and decode helper |
| `event_secondary_price_scale_id` | `bitAnd(event_meta, 4) != 0` | `[B, L]` | categorical id, `0=/100`, `1=/10000` | event categorical embedding and decode helper |
| `event_tape_id` | `bitAnd(bitShiftRight(event_meta, 3), 7)` | `[B, L]` | categorical id, bits 3-5 of `event_meta` | event categorical embedding |
| `event_primary_price_bps` | `price_primary_int` + primary scale bit | `[B, L]` | decoded float price, converted to bps/ticks relative to origin reference price | event numeric projection |
| `event_secondary_price_bps` | `price_secondary_int` + secondary scale bit | `[B, L]` | decoded float price, converted to bps/ticks relative to origin reference price; zero/masked for trade secondary price | event numeric projection |
| `event_primary_size_log1p` | `size_primary` | `[B, L]` | `log1p(max(size_primary, 0))`, optionally clipped/standardized | event numeric projection |
| `event_secondary_size_log1p` | `size_secondary` | `[B, L]` | `log1p(max(size_secondary, 0))`, optionally clipped/standardized | event numeric projection |
| `event_exchange_primary_id` | `exchange_primary` | `[B, L]` | categorical id; byte value 0 means missing/unknown where applicable | event categorical embedding |
| `event_exchange_secondary_id` | `exchange_secondary` | `[B, L]` | categorical id; byte value 0 means missing/unknown where applicable | event categorical embedding |
| `event_condition_token_1_id` | `condition_token_1` | `[B, L]` | dense token id; byte bits 0-7 store one token, `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_2_id` | `condition_token_2` | `[B, L]` | dense token id; byte bits 0-7 store one token, `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_3_id` | `condition_token_3` | `[B, L]` | dense token id; byte bits 0-7 store one token, `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_4_id` | `condition_token_4` | `[B, L]` | dense token id; byte bits 0-7 store one token, `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_5_id` | `condition_token_5` | `[B, L]` | dense token id; byte bits 0-7 store one token, `0=missing/unknown` | event condition-token embedding |
| `event_time_features` | event UTC/session time columns | `[B, L, T_event]` | float32 time components through shared time encoder plus event adapter | event time adapter |
| `event_position_id` | event row rank in sliding stream | `[B, L]` | relative sequence position, newest event has a stable convention | event position embedding |
| `event_mask` | `raw_event_mask` | `[B, L]` | bool mask | event attention mask |
| `ticker_daily_bar_numeric` | `bar_inputs["ticker_daily_bars"]["values"]` | `[B, O, 9]` | prices normalized to origin reference or recent close; volume/count fields in `log1p`; optional z-score | ticker bar encoder |
| `ticker_daily_bar_offset_id` | ticker bar offsets | `[O]` broadcast to `[B, O]` | learned completed-bar offset id | ticker bar encoder |
| `ticker_daily_bar_time_features` | ticker bar time features | `[B, O, T_bar]` | shared time encoder plus bar adapter | ticker bar encoder |
| `ticker_daily_bar_mask` | ticker bar mask | `[B, O]` | bool mask | ticker bar attention mask |
| `global_daily_bar_numeric` | `bar_inputs["global_daily_bars"]["values"]` | `[B, S, O, 9]` | same numeric transforms as ticker daily bars | global bar encoder |
| `global_daily_bar_symbol_id` | global symbol list | `[S]` broadcast to `[B, S, O]` | learned symbol embedding | global bar encoder |
| `global_daily_bar_offset_id` | global bar offsets | `[O]` broadcast to `[B, S, O]` | learned completed-bar offset id | global bar encoder |
| `global_daily_bar_time_features` | global bar time features | `[B, S, O, T_bar]` | shared time encoder plus bar adapter | global bar encoder |
| `global_daily_bar_mask` | global bar mask | `[B, S, O]` | bool mask | global bar attention mask |
| `ticker_news_embedding` | `text_inputs["ticker_news"].embeddings` | `[B, 8, 2, 1024]` | precomputed Qwen embedding projected to `d_model`; Qwen remains offline/frozen | ticker-news encoder |
| `market_news_embedding` | `text_inputs["market_news"].embeddings` | `[B, 16, 2, 1024]` | precomputed Qwen embedding projected to `d_model`; market news means all news | market-news encoder |
| `sec_filing_embedding` | `text_inputs["sec_filings"].embeddings` | `[B, 4, 8, 1024]` | precomputed Qwen embedding projected to `d_model` | SEC text encoder |
| `text_item_time_features` | text item time features | per text group `[B, I, T_text]` | availability/publish/accepted time through shared time encoder plus text adapter | text encoders |
| `text_item_mask` | text item mask | per text group `[B, I]` | bool mask | text item pooling mask |
| `text_chunk_mask` | text chunk mask | per text group `[B, I, C]` | bool mask | text chunk pooling mask |
| `xbrl_value_signed_log` | `xbrl_inputs["value"]` | `[B, 4096]` | signed `log1p(abs(value))`, optionally normalized by tag/unit statistics | XBRL numeric projection |
| `xbrl_fiscal_year` | `xbrl_inputs["fiscal_year"]` | `[B, 4096]` | numeric or small categorical embedding | XBRL encoder |
| `xbrl_period_end_days` | `xbrl_inputs["period_end_days"]` | `[B, 4096]` | numeric age plus period-end time embedding | XBRL encoder |
| `xbrl_category_ids` | XBRL category id fields | field-specific `[B, 4096]` | learned categorical embeddings; id `0=missing/unknown` | XBRL categorical projection |
| `xbrl_mapping_confidence` | `xbrl_inputs["mapping_confidence"]` | `[B, 4096]` | float32 numeric feature | XBRL numeric projection |
| `xbrl_time_features` | XBRL availability time features | `[B, 4096, T_xbrl]` | shared time encoder plus XBRL availability adapter | XBRL encoder |
| `xbrl_period_end_time_features` | XBRL period-end time features | `[B, 4096, T_period]` | separate shared time encoding plus XBRL period adapter | XBRL encoder |
| `xbrl_mask` | `xbrl_inputs["mask"]` | `[B, 4096]` | bool mask | XBRL set mask |
| `corporate_action_category_ids` | action/dividend/currency/frequency ids | field-specific `[B, 128]` | learned categorical embeddings; id `0=missing/unknown` | corporate-action encoder |
| `corporate_action_numeric` | corporate numeric feature tensor | `[B, 128, F_ca]` | split factors, log factors, cash amount, and indicator flags as float32 | corporate-action encoder |
| `corporate_action_available_time_features` | corporate available time features | `[B, 128, T_ca]` | shared time encoder plus corporate availability adapter | corporate-action encoder |
| `corporate_action_effective_time_features` | corporate effective time features | `[B, 128, T_ca_eff]` | shared time encoder plus corporate effective-time adapter | corporate-action encoder |
| `corporate_action_mask` | corporate action mask | `[B, 128]` | bool mask | corporate-action set mask |
| `modality_available_mask` | `input_availability` | `[B, M]` | bool mask plus optional learned missing-modality token | fusion transformer |

### Atomic Model Outputs

Use query tokens for every supervised horizon. Intraday query tokens are keyed
by `future_intraday_bar_horizons`; corporate-action query tokens are keyed by
`corporate_action_label_days`.

| Model output atom | Target source | Output shape | Target transform | Loss |
| --- | --- | --- | --- | --- |
| `ask_delta_bps` | `intraday_labels.price_primary_int` | `[B, H]` | decoded future ask/primary price to bps/ticks vs origin reference | masked Huber/MAE |
| `bid_delta_bps` | `intraday_labels.price_secondary_int` | `[B, H]` | decoded future bid/secondary price to bps/ticks vs origin reference | masked Huber/MAE |
| `primary_size_log1p` | `intraday_labels.size_primary_sum` | `[B, H]` | `log1p(max(size_primary_sum, 0))` | masked Huber/MAE |
| `secondary_size_log1p` | `intraday_labels.size_secondary_sum` | `[B, H]` | `log1p(max(size_secondary_sum, 0))` | masked Huber/MAE |
| `event_count_log1p` | `intraday_labels.event_count` | `[B, H]` | `log1p(event_count)` or Poisson target | masked Huber/Poisson |
| `halt_pause_logit` | `condition_halt_pause_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `resume_logit` | `condition_resume_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `news_risk_logit` | `condition_news_risk_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `luld_limit_state_logit` | `condition_luld_limit_state_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `ticker_news_arrival_logit` | `ticker_news_arrival_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `sec_filing_arrival_logit` | `sec_filing_arrival_flag` | `[B, H]` | bool target | masked BCE-with-logits |
| `future_split_logit` | `future_split_flag` | `[B, D]` | bool target | daily BCE-with-logits |
| `future_reverse_split_logit` | `future_reverse_split_flag` | `[B, D]` | bool target | daily BCE-with-logits |
| `future_forward_split_logit` | `future_forward_split_flag` | `[B, D]` | bool target | daily BCE-with-logits |
| `future_dividend_ex_logit` | `future_dividend_ex_flag` | `[B, D]` | bool target | daily BCE-with-logits |
| `future_special_dividend_ex_logit` | `future_special_dividend_ex_flag` | `[B, D]` | bool target | daily BCE-with-logits |
| `future_any_corporate_action_logit` | `future_any_corporate_action_flag` | `[B, D]` | bool target | daily BCE-with-logits |

Do not train redundant mid-price labels by default. If an experiment wants a
mid or spread target, it should be derived explicitly in the model adapter and
registered as an ablation head with its own loss weight.

## Model Architecture

The v3 model is a set of independent encoders plus a fusion transformer and
horizon heads.

### Research-Backed Architecture Choice

The recommended v3 architecture is:

```text
loader tensors
  -> atomic model data adapter
  -> modality-specific encoders
  -> fixed-size modality latent tokens
  -> bottleneck/cross-attention fusion transformer
  -> intraday and daily horizon query tokens
  -> typed multi-task heads
```

This is the most practical design for our data because each modality has a
different structure: ordered event streams, completed daily-bar sequences,
sets of text embeddings, sets of XBRL facts, sparse corporate-action rows, and
typed future labels. A single concatenated transformer over every raw element is
not the default because it is harder to cache, more expensive for large XBRL/text
sets, and less explicit about missing modalities.

Research basis:

| Source | Relevant result | v3 design implication |
| --- | --- | --- |
| [Perceiver IO](https://arxiv.org/abs/2107.14795) | Uses latent arrays and output queries to handle arbitrary input/output structures with better scaling than full self-attention over every input. | Use cross-attention from each large modality into a small number of latent tokens, then use horizon query tokens for outputs. |
| [Attention Bottlenecks for Multimodal Fusion](https://arxiv.org/abs/2107.00135) | Fusion bottleneck tokens improve multimodal fusion efficiency and force modalities to share compact useful information. | Fuse event/bar/text/XBRL/corporate-action tokens through a small bottleneck rather than unrestricted pairwise attention across every raw item. |
| [Set Transformer](https://arxiv.org/abs/1810.00825) | Attention over sets supports permutation-invariant/equivariant processing and can reduce cost with inducing points. | Use set/pooling or Perceiver-style attention for news items, XBRL rows, and corporate-action rows where row order is by recency but not a physical sequence like events. |
| [Temporal Fusion Transformer](https://arxiv.org/abs/1912.09363) | Multi-horizon forecasting benefits from variable selection, gating, static/context conditioning, and interpretable attention. | Use horizon-specific query tokens, modality gates, and typed heads instead of one undifferentiated output vector. |
| [FT-Transformer](https://arxiv.org/abs/2106.11959) and [TabTransformer](https://arxiv.org/abs/2012.06678) | Tabular models work well when categorical fields are embedded and numerical fields are projected separately before transformer mixing. | Treat event categories, XBRL ids, and corporate-action ids as embeddings; do not concatenate raw ids as numeric values. |
| [PatchTST](https://arxiv.org/abs/2211.14730) | Long time-series transformers benefit from local patching to reduce attention cost while retaining local semantics. | If 1024-event attention is too expensive, patch local event blocks before the event transformer rather than reducing coverage. |
| [DeepLOB](https://arxiv.org/abs/1808.03668) | Market microstructure models benefit from local temporal filters plus longer temporal dependency modeling. | Event encoder should preserve local event ordering and can use TCN/conv front-end before transformer attention. |
| [Flamingo](https://arxiv.org/abs/2204.14198) | Strong multimodal systems bridge pretrained modality encoders with trainable cross-attention while keeping expensive pretrained encoders frozen. | Keep Qwen embedding extraction offline/frozen; train only projection, pooling, fusion, and heads. |

Default implementation choice:

- Event encoder: decode/normalize event numeric fields, embed categorical fields,
  run local temporal mixing plus transformer/attention pooling, emit 8-32 event
  latent tokens.
- Bar encoder: process ticker and global completed bars separately, emit 1-4
  ticker-bar tokens and 1-8 global-bar tokens.
- Text encoders: project Qwen embeddings, pool chunks into items, then pool
  items into one or a few tokens per text group.
- XBRL encoder: use category/numeric/time projections plus Perceiver or Set
  Transformer inducing tokens; emit 4-16 XBRL tokens without full 4096-row
  self-attention by default.
- Corporate-action encoder: lightweight Set/Perceiver pooling; emit 1-4 tokens.
- Fusion: concatenate modality tokens, missing-modality masks, learned bottleneck
  tokens, and horizon query tokens. Run a compact fusion transformer. Decode
  only from query tokens.
- Production caching: cache modality encoder outputs keyed by the modality cache
  state. Recompute fusion/head outputs more often than expensive encoders.

### Time Encoding Contract

Time should be represented consistently across modalities, but not interpreted
as the same semantic object everywhere. The model should use a hybrid time
design:

```text
raw time features
  -> shared TimeEncoder
  -> modality-specific TimeAdapter
  -> add to that modality's content token
```

The shared encoder learns common calendar geometry:

```text
UTC second/day/week/year cycles
years_since_2000
signed source-minus-origin delta
log age to origin
```

The modality adapter lets the model reinterpret the same age differently for
different data. For example, old SEC filings and old XBRL facts can remain
useful long after old breaking news has decayed.

Recommended modules:

```text
CalendarTimeEncoder   small shared MLP/Fourier projection, output 32-64 dims
RelativeAgeEncoder    shared log-age/delta projection, output 16-32 dims
ModalityTimeAdapter   small per-modality linear/MLP to d_model
```

Each modality token should be constructed as:

```text
token = content_projection(x)
      + modality_time_adapter(shared_time_embedding)
      + position_or_rank_embedding
      + modality_embedding
```

Do not use one global sequence-level time encoder that mixes all modality times
before the modality encoders. Event timestamps, text publish times, SEC accepted
times, XBRL period/availability times, corporate-action availability/effective
times, daily-bar offsets, and label horizons have different semantics. Also do
not rely only on raw time-feature
concatenation inside each modality; that makes the time representation
inconsistent and harder to cache.

### Event Encoder

Input:

```text
raw_event_stream [B, 1024, F]
raw_event_mask   [B, 1024]
```

Design:

- numeric projection for price, size, and time features
- categorical embeddings for event type, exchanges, flags, and conditions
- temporal encoder over the 1024-event stream using a transformer or TCN
- output an event modality token and optional event summary sequence

### Bar Encoder

Input:

```text
ticker_daily_bars
global_daily_bars
```

Design:

- separate ticker and global bar encoders
- MLP projection for bar features
- add shared time encoding through a bar-specific adapter
- add learned completed-bar offset embeddings, e.g. `-1d`, `-2d`, `-7d`
- small transformer or attention pooling over offsets/symbols
- output ticker-bar and global-bar modality tokens

### Text Embedding Encoder

Input:

```text
Qwen chunk embeddings + item/chunk masks + timestamps + metadata category ids
```

Design:

- project `1024 -> d_model`
- add modality, item-position, chunk-position, and adapted time embeddings
- pool chunks into item embeddings using masked attention or gated pooling
- pool items into one modality token per group:
  - ticker news
  - market news
  - SEC filings

The model does not fine-tune Qwen in v3. Qwen inference is offline and cached.

### XBRL Encoder

Input:

```text
up to 4096 XBRL rows per sample
```

Design:

- numeric projection for value, period/time features, confidence
- category embeddings for taxonomy, tag, unit, form, row kind, and location
- use separate time embeddings for accepted/availability time and period-end age
- gated pooling or Perceiver-style latent cross-attention
- avoid full 4096-row self-attention by default
- output one XBRL modality token

### Corporate Action Encoder

Input:

```text
up to 128 corporate-action rows per sample
```

Design:

- category embeddings for action type, dividend type, currency, and frequency
- numeric projection for split factors, log factors, cash amount, and indicator bits
- use availability-time embeddings for as-of/source timing
- use effective-time embeddings for the economic event date
- gated pooling or compact cross-attention over the sparse action rows
- output one corporate-action modality token

This encoder should be lightweight. Corporate actions are sparse and the model
mainly needs to know whether a known upcoming or recent split/dividend context
changes the event dynamics and whether future daily corporate-action labels are
likely.

### Fusion Transformer

Input modality tokens:

```text
event
ticker_daily_bars
global_daily_bars
ticker_news
market_news
sec_filings
xbrl
corporate_actions
```

Design:

- add modality embeddings
- include missing-modality masks
- append learned horizon query tokens
- run a fusion transformer
- decode each horizon query through prediction heads

### Prediction Heads

The model should expose prediction heads for every label group emitted by the
loader. A training run can disable a group by setting its loss weight to zero or
by omitting the data group from the loader, but the default full-supervised v3
objective should consume all available labels in the batch.

Prediction heads are grouped by target type so the trainer can use the right
normalization, mask, and loss for each output.

Regression label groups:

```text
intraday_labels.price_primary_int   float32 [B, H]
  target head                       ask_delta_bps or primary_price_delta_bps
  mask                              intraday_labels.available [B, H]

intraday_labels.price_secondary_int float32 [B, H]
  target head                       bid_delta_bps or secondary_price_delta_bps
  mask                              intraday_labels.available [B, H]

intraday_labels.event_count         uint64  [B, H]
intraday_labels.size_primary_sum    float32 [B, H]
intraday_labels.size_secondary_sum  float32 [B, H]
  mask                              intraday_labels.available [B, H]
```

`future_intraday_bars [B,H,5]` remains a loader compatibility projection with
feature order `open, close, high, low, volume`. For v3 loss, do not train three
duplicate primary-price losses from `open/close/high`. Use one primary/ask price
target and one secondary/bid price target, plus explicit size/count heads.

`H` is `len(future_intraday_bar_horizons)`. Price-like targets arrive from the
loader as decoded `float32` price levels; the ticker-month builder has already
applied the scale bits packed in the target event's `event_meta`. These decoded
levels must still be converted to normalized deltas before loss calculation,
preferably in bps or ticks relative to the origin/as-of bid, ask, or mid. Volume
and size targets should be trained in a positive, scale-stable space such as
`log1p`.
`last_event_timestamp_us [B, H]` is diagnostic timing metadata and should not be
part of the default supervised objective unless explicitly enabled.

Classification label groups:

```text
Intraday event-state flags, bool [B, H]:
  condition_halt_pause_flag
  condition_resume_flag
  condition_news_risk_flag
  condition_luld_limit_state_flag
  mask: intraday_labels.available [B, H]

Intraday external-arrival flags, bool [B, H]:
  ticker_news_arrival_flag
  sec_filing_arrival_flag
  mask: intraday_labels.available [B, H]

Corporate-action daily flags, bool [B, D]:
  future_split_flag
  future_reverse_split_flag
  future_forward_split_flag
  future_dividend_ex_flag
  future_special_dividend_ex_flag
  future_any_corporate_action_flag
  default D horizons: +1d, +2d, +3d, +7d, +28d
```

Classification heads must output logits, not probabilities. Corporate-action
labels are dense for emitted origins; if a future loader version adds an
explicit `corporate_action_label_mask`, the trainer must use it. Until then, the
corporate-action mask is true wherever the label group is present in the batch.

Optional diagnostic heads:

- label availability calibration
- spread or liquidity regime classification

Every head must have an explicit loss weight and metric prefix. Zero weight
means the head can be computed for diagnostics without contributing to the
gradient.

## Loss

The v3 trainer should calculate loss over all labels available in the emitted
batch, not only the price targets. Availability is controlled by masks and
presence of label groups, not by a hard-coded list in the trainer.

All task losses use a masked mean:

```text
masked_mean(value, mask) = sum(value * mask) / max(sum(mask), 1)
```

The total loss is the active-weight-normalized weighted sum of task losses:

```text
weighted_loss_sum =
    price_weight             * price_loss
  + event_count_weight       * event_count_loss
  + event_size_weight        * event_size_loss
  + event_state_weight       * event_state_bce
  + external_arrival_weight  * external_arrival_bce
  + corporate_action_weight  * corporate_action_bce

active_weight_sum = sum(weights for tasks with at least one valid target)

total_loss = weighted_loss_sum / max(active_weight_sum, eps)
```

All terms are mask-aware. If a label group is absent from the batch, or if all
targets for a task are masked unavailable, that task contributes zero loss and
reports zero valid count for the step. The trainer should still log raw losses,
weighted losses, valid counts, positive rates for binary labels, and the active
weight sum.

Price loss:

- target shapes: `price_primary_int [B, H]` and `price_secondary_int [B, H]`
- prediction shapes: `ask_delta_bps [B, H]` and `bid_delta_bps [B, H]`
- mask: `intraday_labels.available [B, H]`
- default loss: Huber in normalized bps/tick space
- optional weights: `price_side_weight [2]` and `price_horizon_weight [H]`
- `future_intraday_bars` can be used to access the same labels, but v3 should
  not count duplicate `open/close/high` projections as separate losses

Event-count and size losses:

- target shapes: `event_count [B, H]`, `size_primary_sum [B, H]`,
  `size_secondary_sum [B, H]`
- mask: `intraday_labels.available [B, H]`
- event count: Poisson NLL, log-MAE, or Huber on `log1p(count)`
- size sums: Huber on `log1p(size)`
- count and size predictions must be non-negative after the head transform,
  for example with `softplus`, unless the loss is applied in log space

Binary event-state and arrival losses:

- target shape: one bool tensor `[B, H]` per flag
- logit shape: one float tensor `[B, H]` per flag
- mask: `intraday_labels.available [B, H]`
- loss: masked BCE-with-logits
- event-state group: halt/pause, resume, news-risk, and LULD flags
- external-arrival group: ticker-news and SEC-filing arrival flags
- per-label positive weights should be configurable and capped because these
  targets are sparse

Corporate-action losses:

- target shape: one bool tensor `[B, D]` per corporate-action flag
- logit shape: one float tensor `[B, D]` per corporate-action flag
- default daily horizons: `+1d,+2d,+3d,+7d,+28d`
- loss: masked BCE-with-logits
- flags: split, reverse split, forward split, dividend ex-date, special dividend
  ex-date, and any corporate action
- per-label positive weights should be configurable and capped because
  split/special-dividend targets are very sparse
- optional weights: `corporate_action_day_weight [D]`

Label availability calibration remains optional and should not be enabled by
default unless there is a clear diagnostic need.

## Metrics

Metrics should be emitted to Rich terminal, JSONL, and W&B.

Core training metrics:

- `train/loss`
- `train/primary_loss`
- `train/aux_loss`
- `train/learning_rate`
- `train/grad_norm`
- `train/samples_seen_total`
- `train/samples_per_second`
- `train/step_seconds`
- `train/loader_wait_seconds`
- `train/gpu_step_seconds`
- `train/gpu_memory_allocated_gib`
- `train/gpu_memory_reserved_gib`

Label-derived price metrics, overall and per horizon:

- `mae_bid_bps`
- `mae_ask_bps`
- `rmse_bid_bps`
- `rmse_ask_bps`
- `median_abs_error_bid_bps`
- `median_abs_error_ask_bps`
- `sign_accuracy_bid`
- `sign_accuracy_ask`
- `directional_accuracy_any_move`
- `valid_fraction`
- `target_mean_bps`
- `target_std_bps`
- `prediction_mean_bps`
- `prediction_std_bps`
- `bias_bps`

Spread/liquidity-aware metrics:

- `mae_bid_bps_by_spread_bucket`
- `mae_ask_bps_by_spread_bucket`
- `sign_accuracy_by_spread_bucket`
- `mae_by_event_count_bucket`
- `mae_by_session_bucket`

Intraday label metrics:

- `future_event_count_mae`
- `future_event_count_log_mae`
- `future_size_primary_log_mae`
- `future_size_secondary_log_mae`
- `event_state_bce`
- `event_state_auc` when enough positives/negatives exist
- `event_state_positive_rate`
- `event_state_valid_fraction`
- `external_arrival_bce`
- `external_arrival_auc` when enough positives/negatives exist
- `external_arrival_positive_rate`
- `label_available_fraction`
- `last_event_timestamp_gap_seconds`

Corporate-action label metrics:

- `corporate_action_bce`
- `corporate_action_auc` when enough positives/negatives exist
- `corporate_action_positive_rate`
- `corporate_action_valid_fraction`
- `future_split_flag_bce`
- `future_reverse_split_flag_bce`
- `future_forward_split_flag_bce`
- `future_dividend_ex_flag_bce`
- `future_special_dividend_ex_flag_bce`
- `future_any_corporate_action_flag_bce`

Input availability metrics:

- `ticker_news_available_fraction`
- `market_news_available_fraction`
- `sec_filings_available_fraction`
- `xbrl_available_fraction`
- `ticker_bars_available_fraction`
- `global_bars_available_fraction`
- `event_window_valid_fraction`

State/data accounting metrics:

- `loader/epoch`
- `loader/package_position`
- `loader/origin_cursor`
- `loader/emitted_batches`
- `loader/emitted_samples`
- `loader/seen_origins_total`
- `loader/seen_origins_this_epoch`
- `loader/cache_manifest_fingerprint`
- `loader/dataset_plan_id`

Validation metrics should mirror training metrics with `val/` prefixes and
should be computed on deterministic validation loader state.

## Stateful Trainer Contract

Each checkpoint must contain enough state to resume the same run without
changing data order:

```text
model.state_dict
optimizer.state_dict
scheduler.state_dict
scaler.state_dict
global_step
epoch
samples_seen
best_metric_state
train_loader.state_dict()
validation_loader.state_dict()
python RNG state
numpy RNG state
torch RNG state
cuda RNG state
config snapshot
dataset_id
cache_manifest_fingerprint
git commit
wandb run id
```

Resume flow:

1. Rebuild config and model.
2. Recreate train/validation loaders from config.
3. Verify cache manifest fingerprint.
4. Restore model, optimizer, scheduler, scaler.
5. Restore RNG state.
6. Restore train and validation loader states.
7. Continue from the next batch.

The trainer should expose:

- `--resume-checkpoint`
- `--warm-start-checkpoint`
- `--fresh-start`
- `--dataset-id`
- `--max-origins-per-epoch`
- deterministic hash bucket controls for train/validation/holdout

For benchmarking and hyperparameter search, use fixed dataset ids such as:

```text
temporal_v3_1m_2019_v1
```

The same dataset id, seed, period, hash buckets, and sample limit should produce
identical batches across trainer instances.

## Trainer Engineering

Reuse the v20 training engineering style where practical:

- Rich terminal panels
- W&B metrics
- JSONL metrics
- async checkpoint manager
- run manifest
- failure traceback bundle
- bf16 AMP support
- optional model compile
- model artifact export at run start
- periodic validation
- loader throughput profiling

Data loading should overlap with GPU training:

```text
background loader reads and materializes next batches
GPU trains current batch
checkpoint stores model + optimizer + loader state
```

Default precision:

```text
stored text embeddings: Float32 in ClickHouse/cache
loader CPU tensors: float32 unless memory pressure requires otherwise
GPU training: bf16 AMP by default
```

## Run Artifacts

Each run writes a single run directory. Required files:

```text
config.json
run_manifest.json
metrics.jsonl
logs/fatal_error.txt
checkpoints/
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_summary_torchinfo.txt
artifacts/model/model_summary_training_torchinfo.txt
artifacts/model/model_architecture.md
artifacts/model/model_architecture.mmd
artifacts/model/model_architecture_torchview
artifacts/model/model_architecture_torchview_error.txt
```

If `torchinfo` or `torchview` is unavailable, the trainer must write the matching
`*_error.txt` artifact rather than silently skipping model artifacts.

The model artifact export should include:

- model config
- parameter count by module
- trainable/frozen parameter count
- input/output shape contract
- production inference path summary
- full training path summary
- Mermaid architecture diagram
- optional torchview graph

## Rich Terminal Panels

The terminal should show:

- run summary: run name, dataset id, device, precision, params
- state panel: epoch, global step, samples seen, loader cursor, checkpoint path
- loss/metrics panel: current and moving-average training metrics
- validation panel: latest validation metrics
- throughput panel: samples/s, loader wait, GPU step time, memory
- data availability panel: event/text/XBRL/bar availability fractions
- message panel: recent warnings, checkpoints, validation, audit messages

Panels should be stable and non-flickering, following the v20 Rich layout style.

## Verification Checklist

Before a real training run:

1. Loader emits all requested groups with expected shapes.
2. Text embedding tensors come from `news_text_embeddings` and
   `sec_filing_text_embeddings`, not token ids.
3. Missing text/XBRL/bar context is zero-filled and masked false.
4. Event windows are aligned to `ticker + origin_ordinal`.
5. No origin appears outside the requested train/validation period.
6. Future labels never cross invalid intraday boundaries.
7. Daily bar features use only bars available as of the origin.
8. Future daily labels use only forward bars.
9. Label masks are false when a target horizon is unavailable.
10. The default training objective computes masked losses for every label group
    present in the batch, including price/bar, event-state, external-arrival,
    and corporate-action labels.
11. Checkpoint resume reproduces the exact next batch.
12. Deterministic dataset mode reproduces the same 1M-sample benchmark set.
13. Validation loader is deterministic and independent of train-loader position.
14. Model artifact files are created before training starts.
15. W&B and JSONL metrics contain the same key scalar metrics.
16. Audit can query a small set of sampled identities against ClickHouse and
    verify event rows, labels, bars, text embeddings, and XBRL context.

## Open Implementation Notes

- Text embedding tables must be available before full v3 training.
- XBRL pooling must be designed to avoid quadratic attention over 4096 rows.
- Label normalization constants should be logged in config and manifest.
- Production encoder-cache interfaces should be explicit:
  `encode_events`, `encode_bars`, `encode_text`, `encode_xbrl`, and
  `predict_from_embeddings`.
