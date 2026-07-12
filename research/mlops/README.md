# Research MLOps Utilities

`research/mlops` contains shared engineering utilities used by research model
versions and selected pipelines. Keep reusable infrastructure here, such as
environment loading, ClickHouse helpers, checkpointing, metrics, manifests,
path conventions, seed helpers, W&B setup, and shared event/sample-cache
providers.

The active packed-event loader and reusable multimodal context queries live in
`research/mlops/packed_market`. The old temporal daily-index provider,
chronological Rust loader, offline-batch cache, and generic `mlops/data` trial
stack were removed after their packed-used helpers moved into this package.

Do not add operational market-data workflows here. Market SIP scripts live under
`pipelines/market_sip`, Benzinga workflows under `pipelines/news/benzinga`, SEC
workflows under `pipelines/sec/edgar`, and reference-data workflows under
`pipelines/reference_data`.
