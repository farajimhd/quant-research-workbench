# Current columnar strategy input contract

The future shared path remains QMD History → scanner/watchlist → typed signal
streams → columnar programs → actual Portfolio/OMS/filling broker. Producer
modules prepare shared database products; they are not a separate Backtest
runner or a strategy-owned data source. Existing numbered releases and the
existing previous-session-closes endpoint retain their behavior.

## Opening price reference

`src/market_engine/opening_price_reference.py` centrally defines three normalized
tables: `arte.qmd_opening_price_reference_v1`,
`arte.qmd_opening_price_split_v1` and
`arte.qmd_opening_price_reference_coverage_v1`. Every column is a declared scalar;
there are no JSON/blob columns or strategy-defined tables. Installation is an
operator action, with `live_market_ssd` policy and actual part placement checks.

`OpeningReferencePipeline` in
`pipelines/market_sip/events/opening_price_reference_producer.py` resolves
existing Keeper-backed market-population and central broker/listing identity
certificates. It requires certified prior-session one-second ARTE bars and
effective identity intervals. It reads the last price-eligible close inside
the previous XNYS regular session, including the calendar's early-close bounds.
It applies splits effective after that session and by the current session whose
observations were available by 04:00 America/New_York. Splits are matched on
symbol, listing and security identity; ticker alone never establishes continuity.

An effective identity starting after the prior regular session is an explicit
`excluded_identity` row. A continuous identity with a certified no-trade prior
regular session is `covered_empty`. Missing prior coverage, ambiguous current
identity, inconsistent source hashes, conflicting split ratios or wrong storage
stop publication. Neither exclusion is a synthesized zero price.

Children are inserted and independently read back before the coverage seal.
Source evidence is rechecked before sealing. Exact completed retries perform
reads only. An unsealed attempt or ambiguous write requires operator
reconciliation; it is never silently overwritten or blindly retried. The
operator provides explicit worker capacity and exclusive ownership. Database
work runs in owned workers, with bounded admission and cancellation draining.

`src/backend/qmd_opening_reference.py` provides the SELECT-only cold consumer:
exact source/code/content pins, schema and physical storage, complete population,
child keys/counts/hashes, split arithmetic and an unchanged coverage seal.
Its immutable result is intended for the QMD service/compute funnel; the main
QMD service endpoint and app launch integration are still pending.

## Elapsed market history and scanner

`causal_volume_history.elapsed_volume_history` counts elapsed seconds, including
zero-volume clocks and the current completed second, independently per listing
and session. It rejects gaps, duplicates, future availability, negative or
fractional shares and counts outside exact Float64 integer range.
`volume_rank_rules.select_volume_universe` applies the opening adjusted-price
gate before ranking positive volume descending with listing-identity ties.
The frozen search uses 30 seconds, top 10 and inclusive prices $0.80–$50.
Held positions remain in the actual-state management universe outside this rank.

For search parity, the volume rank uses the certified **share-volume** lane,
whereas `history.attention_mean` uses the certified **execution-notional** lane
from completed liquidity. These are different source fields. Do not substitute
total notional for execution notional or use broker fills as market features.
The frozen history starts at its declared session-clock origin; it must not
silently acquire an extra premarket/overnight warm-up. Empty price clocks remain
missing. Last-price and high/low eligibility masks are independent; a last-only
update does not invalidate otherwise usable close/activity fields.

`columnar_history_product` defines the shared scalar field, completed-clock,
feature-interval and coverage tables on `live_market_ssd`. Its catalog preserves
the original 604 history feature IDs. Preparation joins separately certified
bar and liquidity parents, calculates each selected field once, and stores
unchanged Float32 values as lossless intervals with explicit validity. Bounded
consumer expansion restores dense ordinals, timezone-aware completed clocks,
volume and masks directly consumable by the shared rule evaluator. This is a
product projection; it does not certify caller-supplied parents or publish rows.

The shared history calculation, volume transform and scanner are tested, but
the coverage-last history field product, QMD service access, typed signal-stream
connection, main app actor/recovery wiring and all-30 app comparison remain
required work. Synthetic tests and read-only generated-row SQL qualification
do not establish real-session acceptance. No new strategy number is registered
or published by these modules. Operational installation, publication, producers,
services and real backtests still require the user's prior run approval.
