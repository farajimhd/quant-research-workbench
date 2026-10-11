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
The caller must supply an explicit rule evaluation-block identity. A history
publication attempt does not define a temporal-rule reset. Exact optimizer
comparison must preserve its pinned chunk origin and causal context, including
Float32 cumulative-mean accumulation, before trimming consumer windows.

Feature intervals include a defined nullable UInt32 `value_bits` scalar. It is
the lossless Float32 authority; the nullable Float32 value remains queryable and
must agree numerically with those bits. The publisher constructs Float32 values
through `reinterpretAsFloat32`, avoiding decimal parse drift at numeric edges.
The consumer restores signed zero from the bits before checking ordered hashes.
Real SELECT-only ClickHouse checks cover nulls, adjacent Float32 values, signed
zero and the minimum normal and maximum finite Float32 values.

`columnar_history_producer.CertifiedHistoryInput` binds the effective opening
identity, complete current market plan, verified 100ms liquidity/price parents
and sealed derived 1s liquidity. The operator publisher rechecks source pins,
inserts bounded typed children, verifies every child and key count, recomputes
from the source, and seals coverage last. Ambiguous/unsealed writes require
operator reconciliation. Its explicitly bounded pipeline owns each worker's
connections and accounts for completed receipts even when the caller cancels.
`qmd_columnar_history` supplies a SELECT-only cold consumer with pinned headers,
bounded pagination, full child hashes and unchanged-seal verification.

QMD History exposes `POST /features/columnar-history-window` over these sealed
tables. It accepts an exact source scope, content pin, selected catalog IDs,
elapsed ordinal window and timezone-aware as-of bound. It shares the configured
watchlist compute funnel, returns a retryable busy response instead of queuing
unbounded work, uses asynchronous SELECT-only reads, checks actual SSD placement,
verifies the coverage hash and rechecks the seal after reading. Output consists
of typed column arrays and lossless field intervals. Missing inputs are failures;
this route never invokes a producer or privately calculates features.

The app cold consumer can retain a `CertifiedHistoryPin` without retaining the
audited data frames. `qmd_columnar_history_transport` uses the existing QMD HTTP
transport off the event loop, checks the running service's approved source
fingerprint and declared input contract, and reconstructs dense Polars columns
from interval bits. It verifies header, catalog, clocks, masks and bounds before
the scanner/rule stages. Invalid field values are nullable with explicit masks;
no NaN transport convention is needed. The clock anchor uses New York 04:00's
UTC offset consistently through the supported interval, including DST dates.
The Rust query builder is qualified on generated ClickHouse rows via a bounded
source-hashed SELECT probe; this does not qualify a production table or session.

The shared history calculation, volume transform and scanner are tested, but
current-path scanner and typed signal-stream integration, main app actor/recovery
wiring and all-30 app comparison remain required work. The QMD endpoint still
needs an approved deployment and full-session qualification. Synthetic tests
and read-only generated-row SQL qualification
do not establish real-session acceptance. No new strategy number is registered
or published by these modules. Operational installation, publication, producers,
services and real backtests still require the user's prior run approval.

The shared history product supports an explicit activity-only catalog. Its
revision pins zero feature fields, and its coverage seal records zero feature
rows with the empty-stream hashes. Publication and cold recovery still verify
the same market, opening-reference and liquidity parents, dense clocks and
actual storage placement. Preparing this population skips program-history
calculation; QMD window reads skip feature-table queries and return the same
certified volume clocks. This supplies the scanner population without preparing
program features for every nonparticipant. Feature-bearing products retain
their explicit catalogs and exact masks; missing requested fields still fail.

Historical watchlist plans may opt into schema 5 with a declared
`ranking_tie_field`. The explicit `ranking_tie_type` selects string or UInt64
identity comparison. The frozen optimizer sorts certified listing-ID strings;
its app plan must select `string` to reproduce that lexical order. The generic
UInt64 mode compares integers directly, including IDs above Float64's exact
integer range. Missing
identity evidence fails closed, and incremental app caches include the tie field
in their dependency fingerprint. Schema-4 plans retain their existing hashes
and ordering. A certified population source and current-path plan admission are
still required before this contract can be used by an operational strategy.

`qmd_columnar_population` binds the complete opening certificate to activity-only
history pins for every price-eligible listing. Build, session, market-plan,
opening-reference and elapsed-window identities must agree before any transport
starts. The caller supplies concurrency from its admitted compute funnel; a
fixed worker pool reads bounded QMD windows without one task per listing.
Failures reject the whole window, and cancellation drains owned HTTP reads.
The output accounts for eligible, price-rejected, covered-empty and excluded
identity listings and preserves source revisions, rather than inferring missing
listings as zeros.

The application registry exposes producer-owned rolling share volume and the
opening-known prior regular close as Backtest fields. The existing Reference
Gateway listing identity remains the identity authority; its current registry
metadata now correctly declares canonical identity IDs as strings. Scanner
projection feeds the registered flat fields to the existing Watchlist runtime.
The explicit `on_re_evaluation` policy replaces membership at each evaluation;
existing expiry policies retain their behavior. Synthetic tests exercise QMD
column transport, shared app rules/rank/cache/membership changes and normalized
configuration validation. Main actor and typed-stream orchestration, operational
publication and full-session acceptance remain pending.

The shared runtime now feeds actual typed broker executions to
`AssignedProgramStrategy` after journaling the existing fill/commission facts.
The actor obtains constant-time exact attribution from the existing OMS
broker-ID/request-index views. Account, client order, broker order, conid,
ticker, assignment, intent, side and persisted execution role must agree.
Unknown lineage fails closed; acquisition, profit-target and protective-stop
fills cannot be inferred from ticker or submitted order quantity.

The actor keeps the existing bounded execution-derived position projection;
rule evaluation never receives actual costs, quantities or P&L. Completed
selection uses actual position-open/age and normal management policy. Partial
fills, remainder cancellation and repair-stop exits use normal Portfolio/OMS
and SimulatedBroker behavior. Both 100ms and 1s synthetic runtime tests cover
the actual fill lifecycle, commissions, exact-content deduplication and replay
of the same execution prefix. No new journal table or authority is introduced.
Shared source certificates include this dependency and retain their metadata
anchor, mutation and exact-source checks. Production actor admission, typed
stream/run-controller orchestration and database-backed cold recovery still
require integration and qualification before publication or a real Backtest.

Typed Signal Stream startup now installs the same cold-verified committed cursor
in a fresh shared runtime that the publication queue uses. Configuration and
source revisions, session, sequence, previous commit hash and cutoff remain
bound through staging and promotion. Cursor-codec timestamp formatting is
applied before publication so exact cold readback agrees with the staged state
and routed admissions; equality checks are retained.

Cold SELECT/Keeper bootstrap and receipt waits run off the event loop.
Cancellation drains the owned operation; waiting never promotes state or
dispatches an occurrence. A successful receipt still requires explicit promotion
and the existing dispatch acknowledgement. Synthetic tests cover fresh and
recovered prefixes, routed admission evidence, constant-true rearming, stale
sequence rejection and cancellation on both cold reads and publication. Main
run-controller admission and recovery of committed but undispatched occurrences
remain necessary before operational use.

The main `ReplayRunController` can now construct its existing trading runtime
with an explicit fresh program actor. Release/definition identity, resolved
simulated accounts and the normalized Backtest journal must agree; legacy
restart images, ticker truncation and assignment truncation are rejected for
this binding. Broker liquidity is explicitly selected as 100 or 1000 ms,
independently of program evaluation. Existing app fees and baseline/stress
slippage are preserved. Synthetic tests exercise controller construction,
Portfolio/OMS admission and subsequent actual partial fills at both resolutions.
Construction does not authorize publication or launch: the operational gate
still requires full program input/source orchestration, normalized run settings
and cold recovery qualification before this binding may run an app Backtest.

`ReplayRunController.prepare_program_inputs` now reads cold-certified QMD
history pins for its bound actor's dated listing/ticker identities. The shared
input preparer validates every field's product, revision, unit and cadence,
session/origin and completed clock before starting HTTP reads. A fixed worker
pool reads bounded packets; cancellation drains owned transports. Packets are
joined before the five market-only programs evaluate off the event loop, so
HTTP packet boundaries never reset temporal warm-up or Float32 accumulation.
Preparation does not promote signals, create positions or submit orders.
Synthetic tests compare exact cold-product columns and rule masks across a
4,096-row transport boundary and exercise the main controller's input call.
The run loop still needs durable source/dispatch sequencing, operational source
admission and normalized recovery before real-session qualification.

The controller now binds its isolated Signal Stream runtime through the exact
cold source bootstrap. That runtime retains the verified source commit hashes;
dispatch recovery derives its source bound from that prefix rather than a
caller-supplied sequence. The generic normalized dispatch reader distinguishes
a verified completed ACK prefix from source batches with no dispatch facts.
An all-family tail inventory rejects partial intent/ACK writes. Keeper's closed
gate and exact receipt proofs must agree before a resumed writer reopens the
same prefix and appends the next sequence. Prior receipts and rows are retained;
unresolved INSERT phases still require reconciliation.

Synthetic tests cover source committed before dispatch, completed ACKs followed
by pending source batches, orphan children, changed hashes, duplicate ACKs and
resumed publication without rewriting prior facts. The main controller's cold
source/backlog call is exercised through these shared contracts. Activation,
program boundary dispatch and normalized run recovery still need completion
before the operational launch gate can be opened.

The program controller exposes an assignment-scoped observation of the existing
OMS's processed order facts at the completed runtime clock. Pending acquisition
and reduction state, acknowledged working quantities/prices, missing order
observations and active acquisition high-water marks remain separate. Both
processed LiveOrder and canonical OrderState fingerprint codecs are explicit;
unknown shapes or future order/market observations fail. Requested prices and
quantities are not substituted for broker observations. This projection owns
no table or feature and performs no broker I/O. Tests cover submission without
processed order facts, partial versus complete fills, acknowledged stop coverage
and independence from unacknowledged request changes. Program target-policy
progress, chronological selection/submission and complete run recovery still
require integration before operational admission.
