# Phase-two autoregressive extractor: reviewed replacement design

Status: reviewed specification, not an implemented or launched replacement.
The user cancelled the old phase-two stream dataset and all its repairs, and
explicitly retained the current universe and 75/25 liquidity mix. Monitoring
remains paused. Phase-one repair and training continue independently.

## Verified predecessor

The v3 production model card records 25,674,006,806 origin presentations and a
normally completed run. Its coverage plan contains 6,358,650,082 planned origins
per outer pass, 300 tickers, 25,200 ticker-month units and 4,096 origins per block.
Presentations include chunk repetitions; they are not unique market origins.
The deleted shard payloads' exact size and complete build duration have not been
independently recovered from the retained model card.

Relevant source:

- `v3/direct_event_shards.py`: ticker-owned chronological state; month-partition
  source pages and bounded ordered prefetch.
- `v3/loader.py:build_session_examples`: vectorized shared multiview prefixes,
  contiguous origin blocks and causal as-of indices.
- `v3/offline_shards.py:_merge_view`: deduplication of real bars by timestamp,
  with explicit conflict checks and separate missing-history masks.
- `v3/offline_shards.py:compile_session`: shared arrays plus block slices and
  origin/as-of metadata, rather than repeated full inputs per origin.
- `v3/offline_shards.py:_ticker_worker_main`: extraction/compilation overlap and
  atomic, hash-certified ticker-month publication.

V3's source config default of 512 origins is not its production build setting:
the builder and retained production run use 4,096. Neither value is a token width.

## Preserve the proven extraction structure

Use one chronological owner per ticker across years. Query canonical source
pages with explicit monthly partition bounds; dense pages may be subdivided at
complete-session boundaries under measured memory limits. Query page size and
physical publication size are independent.

Keep a bounded rolling history, warmed once per ticker, not independently for
each ticker-year or small range. Pipeline bounded extraction, vectorized array
compilation and disk publication. A publication is a ticker-month shared-array
shard, split into bounded physical parts only when necessary. Avoid the retired
format's publication/fsync/checkpoint for every small event-budget subdivision.

Pack up to 4,096 core 1s origins into a logical block. This is a storage/loader
grouping, not an encoder window or an instruction to attend over 4,096 origins.
Retain and count partial tails; do not impose a new 4,096-origin eligibility
threshold that silently eliminates sparse ticker-months.

Keep common frame arrays once per shard, with block slices, actual origin clocks
and causal as-of references. Cache the index for cross-month context reads.
Read neighboring source parts for context or future support without duplicating
the same window for every origin. Keep source coverage explicit.

## Replace the old market contract

Historical authority is exclusively `market_sip_compact.events_YYYY`, pinned by
the phase-one canonical dictionary, ordinal inventory and corporate-action
catalog. Reuse current phase-one market semantics and final-bar quarantine.
Do not reuse v3's old trade/quote eligibility SQL or feature projector: its
spread filter, condition treatment and 50-feature schema are different.

Do not reproduce coarser quotes by rolling up only saved trade-bearing 1s bars:
quote updates in seconds without a 1s trade origin can still affect a coarser
bar's quote open, count or carry. The current shared phase-one source aggregation
must remain authoritative for all five output frames.

Store five views: 1s, 10s, 1m, 1h and regular-session daily. Each real bar has:

- 18 Float32 fields in `shards.phase1_contract.CHANNELS` order: trade, bid, ask
  OHLC plus volume/closing size and observed event count;
- 18 explicit validity masks;
- actual UTC bar clocks and completed-bar availability metadata.

No synthetic empty market bars, invented age cutoff, quote-derived origins,
derived indicators, halt channels, old condition-target features or old v3
forecast targets. No embeddings, decoder outputs, per-origin copied 64-bar
samples, scaled model tensors or nine derived UTC inputs are stored.

Raw storage remains in original share basis. The reader applies phase one's
common-origin split adjustment, including quotes and inverse size adjustment,
then phase one's numerical transform. All source price/reference/time metadata
needed for this operation must remain recoverable. Missing evidence is masked.

## Preserve the current scope, separate the learning contract

The retired frozen inventory had 21,556 tickers and 86,235 ticker-year entries,
versus v3's 300-ticker production cohort. The user chose to retain the broader
universe and 75/25 mix. Reuse its authority, not its retired mutable dataset.
Freeze a new manifest from the phase-one authority before generation.

Maintain the chronological boundary inherited from phase one, rather than
copying v3's 2026 validation period. Retain 75/25 liquid/less-liquid exposure
without allowing variable block-tail lengths to distort the origin mix; the
sampling manifest must state its unit and report actual origin exposure.

Do not bake the retired 256-decision geometry, 544/288/160/80 history lengths,
or 38 overlapping encoder views into the raw storage format. The model-ready
manifest separately binds 64-bar token width, historical token stride/count,
forecast target advance, decision sampling stride and target validity.
Context eligibility must cover the selected historical geometry; training
targets must also end before the validation boundary. Missing future support
is explicitly deferred/masked, never taken from another split.

The new format may retain loss-independent future raw bars as source support;
future target arrays/embeddings are constructed by the training reader. The
phase-two objective and exact token stride are still being designed.

## Required delivery gates

Use a fresh versioned output root and preview mode; never revive the retired
root or reuse its catalogue/selection IDs as a new contract. Preserve immutable
source receipts and input contract hashes. The extractor never signals a trainer
and requires an explicit execute command for a production build.

Before a full build: validate real phase-one bar parity, source conditions and
quote masks; origin coverage and continuity; cross-month/year/split boundaries;
as-of causality; raw-to-reader reconstruction; sparse histories and partial
tails; identical pilot/full paths; restart recovery; and bounded RAM/disk use.
Profile matched ticker/month samples and a sparse-universe sample while keeping
training protected. Report source rows, unique bars/origins, output bytes,
query/compile/write time and throughput separately. Do not extrapolate v3's
deleted 4-TB build timing to the larger universe without those measurements.
