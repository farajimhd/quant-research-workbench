# Certified news-reaction targets V1

This is the target-only bridge between the exact event authority and all
embedding experiments. It never reads, recalculates, or writes text embeddings.

Authority:

- `q_live.news_reaction_labels_v3`
- label version `news_reaction_event_labels_v4`
- exact SIP order `(sip_timestamp_us, ordinal)`
- conservative split crossing recorded directly on every label

Identity alignment comes from
`market_sip_compact.news_reaction_openai_stock_state_dataset_v8`, but only its
article identity and publication session are read. The resulting
`market_sip_compact.news_reaction_certified_targets_v1` has one row for every V8
article and only includes horizons with clean, finite targets whose raw
event-derived low/terminal/high returns are internally ordered and which have
no corporate-action crossing. Market-adjusted target values are copied without
pretending that benchmark adjustment at different extrema timestamps preserves
raw price ordering.

Build:

```powershell
python -m research.news_reaction_model.certified_targets_v1.run_build
python -m research.news_reaction_model.certified_targets_v1.run_build --execute
```

The builder checkpoints completed months against a deterministic source
signature. Its final audit proves full article identity coverage, exact target
coverage, aligned array lengths, unique horizons, finite values, return
ordering, and numerical equality to the label authority.

The complete gated rebuild is:

```powershell
python -m research.news_reaction_model.run_certified_v16_v17_build
python -m research.news_reaction_model.run_certified_v16_v17_build --execute
```

It stops at the first failed phase:

1. build and audit the v4 exact-event label authority;
2. build and audit this target-only sidecar;
3. benchmark the corrected V16 bounded reader;
4. build and fully audit V16 v2;
5. build and independently audit V17 v3.

The launcher deliberately does not profile or train either model. Use
`--start-at`, `--stop-after`, and the explicit one-time `--restart-v16` or
`--restart-v17` switches for controlled recovery.
