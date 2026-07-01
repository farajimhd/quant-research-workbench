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
future_bar_values["trade"]      [B, H, 6]
future_bar_masks["trade"]       [B, H]
future_bar_values["quote_bid"]  [B, H, 9]
future_bar_masks["quote_bid"]   [B, H]
future_bar_values["quote_ask"]  [B, H, 9]
future_bar_masks["quote_ask"]   [B, H]
intraday_labels           dict[str, [B, H]]
corporate_action_labels   dict[str, [B, D]]
```

`future_intraday_bars [B,H,5]` remains a trade-family compatibility projection.
Primary price targets should use family-specific trade, bid, and ask fields.
Redundant mid-price targets should not be trained by default unless explicitly
enabled for an ablation.

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
every raw field exactly as stored. A versioned model data adapter should expose
raw typed loader tensors and unpack packed categorical ids into the atomic model
inputs below, while preserving identity fields outside the model for audit and
checkpoint logs. Decoding, normalization, clipping, `log1p`, bps/tick conversion,
and standardization are input-layer preprocessing choices, not loader outputs.

### Atomic Model Inputs

Shape symbols used in the atomic input/output tables:

The event sequence length is written as the numeric value `1024`, matching the
current v3 loader default `event_stream_length`. In event rows, `1024` is the
number of events in the context sequence, not the bit width of a field. Packed
source bits are extraction rules, not tensor axes. For example, `event_meta` bit
0 is unpacked into one scalar `event_type_id` per event. That scalar id is then
embedded to a learned vector before it is fused into the per-event token. If an
experiment changes the loader setting, update the table shapes to the new
numeric value in the model guide/config instead of reusing a symbolic length.

| Symbol | Meaning |
| --- | --- |
| `B` | Batch size emitted by the loader/trainer step. |
| `O` | Number of completed daily-bar offsets requested for ticker/global bar context. |
| `S` | Number of configured global symbols in global daily-bar context. |
| `H` | Number of intraday future horizons in `future_intraday_bar_horizons`. |
| `D` | Number of daily corporate-action label horizons in `corporate_action_label_days`. |
| `F` | Number of raw event fields in loader-level event tensors before the model adapter splits them into atomic event inputs. |
| `I` | Number of as-of external-context items selected for a text group; the value is group-specific, for example ticker news, market news, or SEC filings. |
| `C` | Number of text chunks per external-context item; the value is group-specific. |
| `M` | Number of modality availability flags passed to the fusion path. |
| `T_event` | Number of event timestamp/session time features. |
| `T_bar` | Number of daily-bar time features. |
| `T_text` | Number of text item time features. |
| `T_xbrl` | Number of XBRL availability-time features. |
| `T_period` | Number of XBRL period-end time features. |
| `T_ca` | Number of corporate-action availability-time features. |
| `T_ca_eff` | Number of corporate-action effective-time features. |
| `F_ca` | Number of corporate-action numeric feature dimensions. |
| `d_model` | Model hidden width after each modality-specific projection. |

Event raw storage and adapter chain:

```text
raw_event_stream                [B, 1024, F]
  -> v3 model data adapter      unpack categorical ids and expose raw typed fields
  -> categorical id tensors     [B, 1024] per categorical atom
  -> raw numeric/time tensors   [B, 1024] or [B, 1024, T_*]
  -> input preprocessing layers decode/normalize if enabled
  -> embeddings/projections     [B, 1024, d_atom] per atom/group
  -> event token projection     [B, 1024, d_model]
  -> event encoder              [B, 1024, d_model]
```

For packed event fields, the bit location stays only in the adapter source
definition. The model does not receive a tensor shaped `[B, 1024, bit_0]`.
It receives either scalar category ids shaped `[B, 1024]`, dense numeric
features shaped `[B, 1024]`, or dense vector features shaped `[B, 1024, T_*]`.

Event-atom connection details:

| Event atom | Raw source in `raw_event_stream [B, 1024, F]` | Storage location | Raw/adapter output before input layer | Input-layer preprocessing and learned output | Final event-token connection |
| --- | --- | --- | --- | --- | --- |
| `event_type_id` | `event_meta` | bit 0 | `uint8 [B, 1024]`, vocab size 2 | embedding `[B, 1024, d_event_type]` | concatenate into event token input, then linear projection |
| `event_primary_price_scale_id` | `event_meta` | bit 1 | `uint8 [B, 1024]`, vocab size 2 | embedding `[B, 1024, d_price_scale]` | concatenate into event token input; also controls primary price decode |
| `event_secondary_price_scale_id` | `event_meta` | bit 2 | `uint8 [B, 1024]`, vocab size 2 | embedding `[B, 1024, d_price_scale]` | concatenate into event token input; also controls secondary price decode |
| `event_tape_id` | `event_meta` | bits 3-5 | `uint8 [B, 1024]`, vocab size 8 | embedding `[B, 1024, d_tape]` | concatenate into event token input, then linear projection |
| `price_primary_int` | `price_primary_int` plus primary scale id | full integer column plus scale bit | `uint32/float32 [B, 1024]` raw packed price plus scale id | input layer may decode to float price and normalize to bps/ticks; numeric projection `[B, 1024, d_price]` | concatenate into event token input, then linear projection |
| `price_secondary_int` | `price_secondary_int` plus secondary scale id | full integer column plus scale bit | `uint32/float32 [B, 1024]` raw packed price plus scale id | input layer may decode to float price and normalize to bps/ticks; numeric projection `[B, 1024, d_price]` | concatenate into event token input, then linear projection |
| `size_primary` | `size_primary` | full float column | `float32 [B, 1024]` raw event size | input layer may apply `log1p`, clipping, or standardization; numeric projection `[B, 1024, d_size]` | concatenate into event token input, then linear projection |
| `size_secondary` | `size_secondary` | full float column | `float32 [B, 1024]` raw event size | input layer may apply `log1p`, clipping, or standardization; numeric projection `[B, 1024, d_size]` | concatenate into event token input, then linear projection |
| `event_exchange_primary_id` | `exchange_primary` | full byte column | `uint8 [B, 1024]`, exchange vocabulary | embedding `[B, 1024, d_exchange]` | concatenate into event token input, then linear projection |
| `event_exchange_secondary_id` | `exchange_secondary` | full byte column | `uint8 [B, 1024]`, exchange vocabulary | embedding `[B, 1024, d_exchange]` | concatenate into event token input, then linear projection |
| `event_condition_token_1_id` | `condition_token_1` | full byte column | `uint8 [B, 1024]`, condition vocabulary | embedding `[B, 1024, d_condition]` | mask-aware pooled with other condition slots into one condition feature |
| `event_condition_token_2_id` | `condition_token_2` | full byte column | `uint8 [B, 1024]`, condition vocabulary | embedding `[B, 1024, d_condition]` | mask-aware pooled with other condition slots into one condition feature |
| `event_condition_token_3_id` | `condition_token_3` | full byte column | `uint8 [B, 1024]`, condition vocabulary | embedding `[B, 1024, d_condition]` | mask-aware pooled with other condition slots into one condition feature |
| `event_condition_token_4_id` | `condition_token_4` | full byte column | `uint8 [B, 1024]`, condition vocabulary | embedding `[B, 1024, d_condition]` | mask-aware pooled with other condition slots into one condition feature |
| `event_condition_token_5_id` | `condition_token_5` | full byte column | `uint8 [B, 1024]`, condition vocabulary | embedding `[B, 1024, d_condition]` | mask-aware pooled with other condition slots into one condition feature |
| `event_time_features` | UTC/session time feature columns | full float columns | `float32 [B, 1024, T_event]` | time adapter `[B, 1024, d_time]` | concatenate into event token input, then linear projection |
| `event_position_id` | derived from context order | not stored | `int64 [B, 1024]` | embedding `[B, 1024, d_position]` | add to event token |
| `event_mask` | derived from valid context rows | not stored | `bool [B, 1024]` | no embedding | attention mask for event encoder |

The final event token is built by combining the learned outputs above:

```text
event_token_input =
  LinearProject(
    concat(
    event_type_emb,
    price_scale_embs,
    tape_emb,
    price_input_projection,
    size_input_projection,
    exchange_embs,
    condition_slot_projection,
    event_time_projection
    )
  )
  + position_emb
```

This yields `float32/bf16 [B, 1024, d_model]` before the event encoder.

| Model input atom | Loader/adaptor source | Tensor entering input layer | Loader/adaptor representation | Input-layer responsibility |
| --- | --- | --- | --- | --- |
| `event_type_id` | unpack `raw_event_stream.event_meta` bit 0 | `uint8 [B, 1024]` | one scalar category per event; vocab size 2: `0=quote`, `1=trade` | event categorical embedding |
| `event_primary_price_scale_id` | unpack `raw_event_stream.event_meta` bit 1 | `uint8 [B, 1024]` | one scalar category per event; vocab size 2: `0=/100`, `1=/10000` | event categorical embedding and decode helper |
| `event_secondary_price_scale_id` | unpack `raw_event_stream.event_meta` bit 2 | `uint8 [B, 1024]` | one scalar category per event; vocab size 2: `0=/100`, `1=/10000` | event categorical embedding and decode helper |
| `event_tape_id` | unpack `raw_event_stream.event_meta` bits 3-5 | `uint8 [B, 1024]` | one scalar category per event; values `0..7` | event categorical embedding |
| `price_primary_int` | read `raw_event_stream.price_primary_int` plus primary scale id | `uint32/float32 [B, 1024]` | raw packed quote ask or trade price; scale is not applied by the loader | input layer decides whether to decode to float price and normalize to bps/ticks before numeric projection |
| `price_secondary_int` | read `raw_event_stream.price_secondary_int` plus secondary scale id | `uint32/float32 [B, 1024]` | raw packed quote bid price or zero for trade; scale is not applied by the loader | input layer decides whether to decode to float price and normalize to bps/ticks before numeric projection; trade secondary should be masked/zeroed |
| `size_primary` | read `raw_event_stream.size_primary` | `float32 [B, 1024]` | raw quote ask size or trade size from loader | input layer decides whether to use raw size, `log1p`, clipping, or standardization before numeric projection |
| `size_secondary` | read `raw_event_stream.size_secondary` | `float32 [B, 1024]` | raw quote bid size or zero for trade from loader | input layer decides whether to use raw size, `log1p`, clipping, or standardization before numeric projection; trade secondary should be masked/zeroed |
| `event_exchange_primary_id` | read `raw_event_stream.exchange_primary` | `uint8 [B, 1024]` | one scalar exchange category per event; byte value 0 means missing/unknown where applicable | event categorical embedding |
| `event_exchange_secondary_id` | read `raw_event_stream.exchange_secondary` | `uint8 [B, 1024]` | one scalar exchange category per event; byte value 0 means missing/unknown where applicable | event categorical embedding |
| `event_condition_token_1_id` | read `raw_event_stream.condition_token_1` | `uint8 [B, 1024]` | one scalar dense condition/indicator token per event; `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_2_id` | read `raw_event_stream.condition_token_2` | `uint8 [B, 1024]` | one scalar dense condition/indicator token per event; `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_3_id` | read `raw_event_stream.condition_token_3` | `uint8 [B, 1024]` | one scalar dense condition/indicator token per event; `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_4_id` | read `raw_event_stream.condition_token_4` | `uint8 [B, 1024]` | one scalar dense condition/indicator token per event; `0=missing/unknown` | event condition-token embedding |
| `event_condition_token_5_id` | read `raw_event_stream.condition_token_5` | `uint8 [B, 1024]` | one scalar dense condition/indicator token per event; `0=missing/unknown` | event condition-token embedding |
| `event_time_features` | select event UTC/session time columns | `float32 [B, 1024, T_event]` | one dense time-feature vector per event through shared time encoder plus event adapter | event time adapter |
| `event_position_id` | derive row rank in sliding event stream | `int64 [B, 1024]` | one relative sequence-position id per event; newest event has a stable convention | event position embedding |
| `event_mask` | derive from `raw_event_mask` | `bool [B, 1024]` | true for valid events, false for padded/missing positions | event attention mask |
| `ticker_daily_trade_bar_numeric` | transform `bar_inputs["ticker_daily_bars"]["trade_values"]` | `float32 [B, O, 6]` | trade OHLC, size sum, and count fields; prices normalized to origin reference or recent close; size/count fields in `log1p` | ticker bar encoder |
| `ticker_daily_quote_bid_bar_numeric` | transform `bar_inputs["ticker_daily_bars"]["quote_bid_values"]` | `float32 [B, O, 9]` | bid OHLC plus bid size state fields; prices normalized to origin reference or recent close | ticker bar encoder |
| `ticker_daily_quote_ask_bar_numeric` | transform `bar_inputs["ticker_daily_bars"]["quote_ask_values"]` | `float32 [B, O, 9]` | ask OHLC plus ask size state fields; prices normalized to origin reference or recent close | ticker bar encoder |
| `ticker_daily_bar_offset_id` | broadcast configured ticker bar offsets | `int64 [B, O]` | one completed-bar offset category per ticker daily bar | ticker bar encoder |
| `ticker_daily_bar_time_features` | select ticker bar time features | `float32 [B, O, T_bar]` | one dense time-feature vector per ticker daily bar | ticker bar encoder |
| `ticker_daily_bar_family_masks` | read `trade_mask`, `quote_bid_mask`, `quote_ask_mask` | each `bool [B, O]` | true for available completed bars in each bar family | ticker bar attention mask |
| `global_daily_trade_bar_numeric` | transform `bar_inputs["global_daily_bars"]["trade_values"]` | `float32 [B, S, O, 6]` | same numeric transforms as ticker trade bars | global bar encoder |
| `global_daily_quote_bid_bar_numeric` | transform `bar_inputs["global_daily_bars"]["quote_bid_values"]` | `float32 [B, S, O, 9]` | same numeric transforms as ticker bid bars | global bar encoder |
| `global_daily_quote_ask_bar_numeric` | transform `bar_inputs["global_daily_bars"]["quote_ask_values"]` | `float32 [B, S, O, 9]` | same numeric transforms as ticker ask bars | global bar encoder |
| `global_daily_bar_symbol_id` | broadcast configured global symbol ids | `int64 [B, S, O]` | one learned global-symbol category per global daily bar | global bar encoder |
| `global_daily_bar_offset_id` | broadcast configured global bar offsets | `int64 [B, S, O]` | one completed-bar offset category per global daily bar | global bar encoder |
| `global_daily_bar_time_features` | select global bar time features | `float32 [B, S, O, T_bar]` | one dense time-feature vector per global daily bar | global bar encoder |
| `global_daily_bar_family_masks` | read `trade_mask`, `quote_bid_mask`, `quote_ask_mask` | each `bool [B, S, O]` | true for available completed bars in each bar family | global bar attention mask |
| `ticker_news_embedding` | read `text_inputs["ticker_news"].embeddings` | `bf16/float32 [B, 8, 2, 1024]` | precomputed Qwen embedding projected to `d_model`; Qwen remains offline/frozen | ticker-news encoder |
| `market_news_embedding` | read `text_inputs["market_news"].embeddings` | `bf16/float32 [B, 16, 2, 1024]` | precomputed Qwen embedding projected to `d_model`; market news means all news | market-news encoder |
| `sec_filing_embedding` | read `text_inputs["sec_filings"].embeddings` | `bf16/float32 [B, 4, 8, 1024]` | precomputed Qwen embedding projected to `d_model` | SEC text encoder |
| `text_item_time_features` | select text item time features | per text group `float32 [B, I, T_text]` | availability/publish/accepted time through shared time encoder plus text adapter | text encoders |
| `text_item_mask` | read text item mask | per text group `bool [B, I]` | true for available text items | text item pooling mask |
| `text_chunk_mask` | read text chunk mask | per text group `bool [B, I, C]` | true for available text chunks | text chunk pooling mask |
| `xbrl_value_signed_log` | transform `xbrl_inputs["value"]` | `float32 [B, 4096]` | signed `log1p(abs(value))`, optionally normalized by tag/unit statistics | XBRL numeric projection |
| `xbrl_fiscal_year` | read/transform `xbrl_inputs["fiscal_year"]` | `int16/float32 [B, 4096]` | numeric year feature or small categorical id, as chosen by the v3 adapter config | XBRL encoder |
| `xbrl_period_end_days` | transform `xbrl_inputs["period_end_days"]` | `float32 [B, 4096]` | numeric age plus period-end time embedding | XBRL encoder |
| `xbrl_category_ids` | read XBRL category id fields | field-specific `int64 [B, 4096]` | learned categorical embeddings; id `0=missing/unknown` | XBRL categorical projection |
| `xbrl_mapping_confidence` | read `xbrl_inputs["mapping_confidence"]` | `float32 [B, 4096]` | confidence score feature | XBRL numeric projection |
| `xbrl_time_features` | select XBRL availability time features | `float32 [B, 4096, T_xbrl]` | shared time encoder plus XBRL availability adapter | XBRL encoder |
| `xbrl_period_end_time_features` | select XBRL period-end time features | `float32 [B, 4096, T_period]` | separate shared time encoding plus XBRL period adapter | XBRL encoder |
| `xbrl_mask` | read `xbrl_inputs["mask"]` | `bool [B, 4096]` | true for available XBRL rows | XBRL set mask |
| `corporate_action_category_ids` | read action/dividend/currency/frequency ids | field-specific `int64 [B, 128]` | learned categorical embeddings; id `0=missing/unknown` | corporate-action encoder |
| `corporate_action_numeric` | transform corporate numeric feature tensor | `float32 [B, 128, F_ca]` | split factors, log factors, cash amount, and indicator flags | corporate-action encoder |
| `corporate_action_available_time_features` | select corporate available time features | `float32 [B, 128, T_ca]` | shared time encoder plus corporate availability adapter | corporate-action encoder |
| `corporate_action_effective_time_features` | select corporate effective time features | `float32 [B, 128, T_ca_eff]` | shared time encoder plus corporate effective-time adapter | corporate-action encoder |
| `corporate_action_mask` | read corporate action mask | `bool [B, 128]` | true for available corporate-action rows | corporate-action set mask |
| `modality_available_mask` | read `input_availability` | `bool [B, M]` | true when a modality is available; may also select a learned missing-modality token | fusion transformer |

### Atomic Model Outputs

Use query tokens for every supervised horizon. Intraday query tokens are keyed
by `future_intraday_bar_horizons`; corporate-action query tokens are keyed by
`corporate_action_label_days`.

| Model output atom | Target source | Output shape | Target transform | Loss |
| --- | --- | --- | --- | --- |
| `trade_bar_delta_bps` | `future_bar_values["trade"][..., open/close/high/low]` | `[B, H, 4]` | decoded future trade OHLC to bps/ticks vs origin reference | masked Huber/MAE |
| `trade_size_log1p` | `future_bar_values["trade"][..., size_sum]` | `[B, H]` | `log1p(max(size_sum, 0))` | masked Huber/MAE |
| `trade_event_count_log1p` | `future_bar_values["trade"][..., event_count]` | `[B, H]` | `log1p(event_count)` or Poisson target | masked Huber/Poisson |
| `quote_bid_bar_delta_bps` | `future_bar_values["quote_bid"][..., open/close/high/low]` | `[B, H, 4]` | decoded future bid OHLC to bps/ticks vs origin reference | masked Huber/MAE |
| `quote_bid_size_log1p` | `future_bar_values["quote_bid"][..., size_open/size_close/size_high/size_low]` | `[B, H, 4]` | `log1p(max(size, 0))` | masked Huber/MAE |
| `quote_bid_event_count_log1p` | `future_bar_values["quote_bid"][..., event_count]` | `[B, H]` | `log1p(event_count)` or Poisson target | masked Huber/Poisson |
| `quote_ask_bar_delta_bps` | `future_bar_values["quote_ask"][..., open/close/high/low]` | `[B, H, 4]` | decoded future ask OHLC to bps/ticks vs origin reference | masked Huber/MAE |
| `quote_ask_size_log1p` | `future_bar_values["quote_ask"][..., size_open/size_close/size_high/size_low]` | `[B, H, 4]` | `log1p(max(size, 0))` | masked Huber/MAE |
| `quote_ask_event_count_log1p` | `future_bar_values["quote_ask"][..., event_count]` | `[B, H]` | `log1p(event_count)` or Poisson target | masked Huber/Poisson |
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

- Event input layers: unpack categorical ids, optionally decode/normalize raw
  event numeric fields, embed/project all event atoms, and emit
  `[B, 1024, d_model]` event tokens.
- Event encoder: consume event tokens, run local temporal mixing plus
  transformer/attention pooling, and emit 8-32 event latent tokens.
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
future_bar_values["trade"]          float32 [B, H, 6]
  fields                            open, close, high, low, size_sum, event_count
  mask                              future_bar_masks["trade"] [B, H]

future_bar_values["quote_bid"]      float32 [B, H, 9]
  fields                            open, close, high, low, size_open, size_close, size_high, size_low, event_count
  mask                              future_bar_masks["quote_bid"] [B, H]

future_bar_values["quote_ask"]      float32 [B, H, 9]
  fields                            open, close, high, low, size_open, size_close, size_high, size_low, event_count
  mask                              future_bar_masks["quote_ask"] [B, H]
```

`future_intraday_bars [B,H,5]` remains a loader compatibility projection with
feature order `open, close, high, low, volume`, now projected from the `trade`
family. It is not the canonical v3 target contract.

`H` is `len(future_intraday_bar_horizons)`. Price-like targets arrive from the
loader as decoded `float32` price levels; the ticker-month builder has already
applied the scale bits packed in each source event's `event_meta`. These decoded
levels must still be converted to normalized deltas before loss calculation,
preferably in bps or ticks relative to the origin/as-of bid, ask, or mid. Size
and count targets should be trained in a positive, scale-stable space such as
`log1p`.

Intraday future labels are grid-aligned, not exact origin-relative windows. The
builder skips the origin's current partial bucket and emits
`label_resolution_us`, `label_grid_start_timestamp_us`, and
`label_grid_end_timestamp_us` in `intraday_labels`. The model should not consume
raw grid timestamps by default, but audits and diagnostic plots should use them
to explain the effective target window.
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

Bar price loss:

- target shapes: trade `[B, H, 4]`, quote_bid `[B, H, 4]`, quote_ask `[B, H, 4]`
- prediction shapes: same family/field shapes in normalized bps/tick space
- masks: `future_bar_masks[family] [B, H]`
- default loss: Huber in normalized bps/tick space
- optional weights: `bar_family_weight [3]`, `bar_price_field_weight [4]`, and `price_horizon_weight [H]`
- `future_intraday_bars` is compatibility output and should not add a duplicate loss

Event-count and size losses:

- target shapes: trade size/count from `[B, H, 6]`; quote bid/ask size state and count from `[B, H, 9]`
- masks: `future_bar_masks[family] [B, H]`
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
