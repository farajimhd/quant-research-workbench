# Point-in-time reference research channels

`reference_channels` aligns complete upstream-selected reference snapshots with
decisions using vectorized Polars as-of joins. All clocks are UTC epoch
milliseconds. Availability is the latest publication, recording, insertion and
identity-recording clock. Channels, freshness and packet bounds are explicit
parameters. Missing and stale snapshots remain unavailable, with null channels.
Conflicting snapshots at the same availability clock fail closed.

Upstream code must select XBRL periods, units and amendments causally and certify
point-in-time symbol identity. Split announcement and effective clocks must be
distinct channels: a known future split must not adjust past candles. This join
preserves those fields without treating a known announcement as an effective
event. No news or candle adjustment is performed.

This is a research projection, not a certified product or native strategy.
Historical coverage, fundamental/split ingestion, big-move comparisons with
these channels and full-session financial/performance acceptance remain open.
