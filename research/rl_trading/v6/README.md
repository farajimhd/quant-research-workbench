# RL trading V6: sparse candles and bracketed portfolio decisions

This is a major data and action-contract revision. It replaces the old
Phase 1/2/3 names with three responsibilities:

1. **Candle and Opportunity Compilation** is ticker-local. Read pinned ARTE
   one-second OHLC, notional, volume, trades, and technical indicators once;
   encode actual persisted candles, causal V7 slots, time/session, VWAP,
   same-clock previous-session RVOL, and point-in-time reference fields.
   Compile MACD episodes and fee-aware, discounted candidate scores in the
   same ticker pass. The future close outcome is label-only.
2. **Portfolio Teacher** compares only sparse eligible opportunities across
   listings. It reserves initial-bankroll cash for first-eligible episodes in
   a 15-second window, has no fixed maximum number of positions, and uses a
   three-second minimum hold. It must bind confirmed fills and bracket labels
   before its output may supervise a model. Old V8 artifacts are not V6 labels.
3. **Causal Policy and Replay** consumes the last 120 *actual candles* per
   listing, including a prior certified session's tail where available. It
   learns entry, exit, hold, stop, target, and size from its action/holding
   history. Replay compounds actual model-account cash subject to the existing
   equity/price order-size rules; teacher bankroll segregation does not apply.

The packed bank stores each candle exactly once, as contiguous
`[sum_listing_candles, 37]` scalars and
`[sum_listing_candles, 2 sides, 5 levels, 11 fields]` V7 attributes with
identity offsets and int64 candle close clocks. It does not store overlapping
120-candle windows or pad every listing to 57,601 clock seconds. A reader
references the previous bank for warm-up. `complete.json` binds the plan,
feature schema, shapes, and file hashes. A partial bank has a separate plan
and per-listing progress hashes and can resume only under the same source and
census.

V7 sides mean the five nearest centers below/equal to or above the completed
close, regardless of support/resistance role. Each level records center/lower/
upper distance, total and today-only observation counts, confirmation age,
role, historical origin, and presence. Empty slots are masked. Today's count
separates streaming evidence from carried checkpoint evidence without
duplicating separate groups of levels.

The bracket oracle and sparse 100 ms event kernel are versioned separately.
The certified ARTE execution-price sidecar supplies per-price volume for
possible target fills. It does not reveal queue position; a touch alone is
not booked as a fill. Future episode extrema never enter policy features.

Production builders remain stopped while this contract is completed and a
bounded one-day canary audits source identity, label/replay parity, throughput,
memory, and outcome counts. No V6 model is trained by these modules alone.
