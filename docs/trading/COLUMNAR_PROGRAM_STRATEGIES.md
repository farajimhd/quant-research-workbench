# Shared columnar program strategies

## Current qualification

The V19 importer, native app audit composition and shared QMD input preparation
are implemented. Full-session app-versus-optimization qualification is still in
progress. Source tests or an optimizer reference replay do not certify an app
broker run. Replay, paper and live admission must remain closed until the
concrete durable assembly and its source context pass their qualification.

## One native compute and execution path

QMD History preserves the exact Float64 clock values across database JSON and
HTTP transport. The gateway enables round-trip float parsing; scanner/history
equality checks remain exact. A transport correction does not rebuild or relabel
the sealed input products.

The current path is:

1. Certified canonical ClickHouse market products feed QMD History.
2. The shared scanner ranks the complete price-eligible population by completed
   30-second share volume, descending, with listing identity as the tie-break.
   The price filter is the split-adjusted prior regular-session close, inclusive
   of $0.80 through $50. The first version retains the optimization's top 10.
3. Configured Signal Streams consume the scanner projection. Calendar date and
   an explicit execution context distinguish independent runs on the same day.
4. Native Polars expressions evaluate market-only program DAGs in bounded
   blocks. The chronological coordinator selects flat/open branches and age
   gates from actual filled position state and observed OMS state.
5. The existing Portfolio admits semantic capital requests. The existing OMS
   creates and manages orders. The selected filling broker owns liquidity,
   execution prices, partial fills and commissions.

A program does not obtain actual entry price, realized/unrealized position P&L
or other execution-derived features. Position-open state and position age are
explicit coordinator inputs. Stops, targets and other execution policies remain
native stateful management contracts; program masks do not fabricate fills.
Held listings remain manageable after leaving the current top 10.

## Shared input authorities

`qmd_volume_product.py` defines the two scalar `q_live` volume interval/coverage
contracts. `qmd_volume_producer.py` derives them from certified native one-second
bars, preserving explicit complete clock coverage, including zero-volume runs.
It does not require preparing broker liquidity for every scanner listing.

`qmd_volume_reader.py` cold-verifies immutable intervals, source identities and
seals. `qmd_columnar_population.py` requires the complete eligible population;
absence is never interpreted as zero. Snapshot expansion is bounded and runs
off the app event loop. The population cache retains intervals rather than a
full dense population-by-session frame.

Native feature preparation owns continuous history, cross-sectional context,
listing identity, reference availability and the shared feature catalog.
Features may be prepared for the union of listings ranked during the requested
session, but input admission must independently prove that this union is fully
covered. Strategies receive only the current causal boundary. Preparing a
coverage union is not permission to expose future membership or features.

`ProgramInputPublication` composes these existing producers with bounded owned
clients. Children precede seals. Ambiguous writes retain their attempts and
require exact reconciliation; no data deletion, implicit retry or raw SIP
fallback is permitted. Imported days use canonical ClickHouse authorities.
Research shards are comparison evidence and never app market inputs.

Concurrent price publishers use an explicit selected-source certificate. Logical
coverage and child hashes are fenced before and after the full audit, together
with schema and SSD placement; unrelated writes and unchanged physical merges
do not invalidate another listing. Static backtests retain their existing cache.
History publishers may reuse a bounded cohort-owned opening proof only while
its exact source, schema, part checksums and Keeper receipts remain unchanged.
Any parent change requires the full proof again. Child materialization, exact
readback and coverage-last publication remain mandatory for every listing.

Retained structural seeds remain immutable across repeated feature projections;
each consuming candle encoder receives a private copy, including across splits.
An unsealed history may be reconciled only with an explicit existing attempt and
projected content hash. The managed producer rechecks its complete children and
source projection before publishing coverage; it never silently reinserts or
deletes those children.

Continuous history uses the explicitly selected identity basis for every source
day. Shared Reference Gateway captures are bound to that day's certified market
identity, routing and tradability facts, including historical ticker changes.
Their scalar dependencies name the actual source tables and are cold-verified
again before publication. A current opening proof cannot stand in for earlier
source days, and an absent shared proof cannot fall back to symbol intervals.
A historical day may leave continuous state unchanged only when the attested
full-universe source plan proves that the timely pinned tradable listing had no
canonical events. This covered-empty case retains explicit zero-unit, snapshot,
identity and shared-reference dependencies and cold-verifies them again. It
cannot cover missing current-day data, an explicit ticker subset, an unknown
snapshot or a contradictory market/identity unit.

## Immutable translation and execution parameters

`optimized_candidate_evidence.py` freezes the selected generation's programs,
source identities and all recorded training/validation session receipts.
`optimized_candidate.py` translates validated V19 programs into the shared
release schema. `optimized_strategy_import.py` verifies the resulting native
release against the frozen candidate.

The initial release preserves $1,000 entry cash, $200 add cash, $0.005/share
commission with a $1 minimum per order and zero additional cost bps. Native
Portfolio cash, allocations and OMS admission still apply. Cash amounts are
release parameters: changing them creates a new immutable release. Costs and
fees participate in position and run P&L.

Execution binding and risk selection use normalized shared configuration
contracts. Broker liquidity resolution is an explicit backtest parameter;
this audit selects 100ms. Choosing a one-second liquidity product does not
change the program's one-second evaluation cadence or its input causality.
The certified market parent retains the dependency compiler's full resolution
set, including one-second strategy bars. A 100ms broker reads an explicit
parent-bound 100ms projection through the same native market/price reader;
strategy-resolution rows are rejected before reaching the broker. The full
parent and execution authority remain pinned in the journal.
Session-end exit requests must precede the last eligible liquidity bar so the
existing broker can attempt fills. An unfilled request is not a position exit.

## Persistence and ownership

All trading authorities use centrally declared, scalar ClickHouse schemas.
Strategies cannot create tables. JSON/blob columns, SQLite journal authority
and disk-authoritative trading state are prohibited. Runtime receipts and
exported audit artifacts are evidence, not replacements for database authority.
Operational tables and active parts require `live_market_ssd`; the default
backup disk is never an operational fallback.

The current generic V4 assembly requires an installed release/configuration,
actual native run ownership, a durable journal, receipt-gated Portfolio state
and broker commands. Shared account/group ownership must protect capital
across runs. A committed snapshot and Keeper proof precede installing the new
Portfolio image. Uncertain publication or broker outcomes block admission
until explicit recovery; capability flags cannot authorize another mode.

Signal Stream context includes the real native run identity, execution mode,
calendar date, configuration revision and source revision. Contextual shared
contracts preserve existing v1 records and codecs. New current-path runs must
use context-bound source and dispatch ownership and cold recovery. A date
change or a strategy-specific table is not a way to avoid a collision.

## Audit process

`optimized_audit_run.py` composes the existing `ReplayRunService` and installed
configuration selector. `optimized_audit_collection.py` collects actual typed
native decisions, positions, orders, fills, fees and full-session metrics.
`optimized_execution_audit.py` reports bounded, keyed stage differences.

The source-model tracer invokes the unchanged V19 transition oracle, verifies
its metrics against replay and can stream full-session evidence in bounded
batches. It labels optimizer orders and execution IDs unavailable rather than
inventing broker facts. This is reference evidence, not a second app broker.

Compare inputs, membership and raw rule outputs independently of broker state.
Compare sequential decisions only where the pre-decision states are equivalent.
Then attribute order, fill, position and P&L differences to their first proven
cause. Preserve exact Decimal evidence separately from explicitly converted
comparison numbers. Do not silently add tolerances or force broker results to
match assumed immediate fills.

All frozen optimization session results remain in the comparison report.
Unexecuted app sessions are explicitly unrun. Profitability selection and new
validation experiments are a later phase; matching an audit session alone does
not establish profitability or live suitability.

Clock metadata may declare one physical input parent per split or an exact
session-to-physical-parent mapping. A dataset directory junction does not
authorize its target implicitly. Every declared session still requires its
original certificate and clock hashes, complete clock grid and containment
inside the declared physical parent. These files provide comparison clocks,
never app market observations.
