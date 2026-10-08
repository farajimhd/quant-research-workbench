# Native causal channel producer contract

`native_causal_channel_contract.py` declares the versioned normalized tables
`arte.native_causal_channels_v1` and `arte.native_causal_channel_coverage_v1`.
The producer reads certified, pinned `arte.bars_v1` attempts only. Its public
function produces typed Arrow packets and content witnesses; it performs no
installation, publication or execution admission.

Declare native source resolutions, a native decision interval, freshness for
each resolution, and prior-bar participation and volatility lookbacks through
`NativeChannelPolicy`. All parameters contribute to the policy digest. Rows
retain absolute native integer OHLC and participation alongside v4 returns,
relative volume/trade counts, volatility-normalized shapes and source timing.
Source clocks are milliseconds since New York source midnight. Current candles
are excluded from their own trailing baselines. Gaps, invalid prices and zero
baselines yield null relative channels; no future labels are accepted. Earlier
regular-session source remains available for AH normalization. This product
does not replace AH's separate prior-day V7 checkpoint and complete RTH warming.

Requests are bounded to eight tickers and two million possible source rows.
The source SELECT has an overflow sentinel and honors the reader's configured
resource policy. Normalization uses Polars expressions. Small loops seal
ticker/resolution partitions; they do not implement sequential market decisions.
Coverage includes each requested ticker/resolution, including zero source rows,
and pins the bar source/output hashes, market token, producer-source identity,
typed content hashes, row counts and completed boundary. Missing market source
certification is fatal before the source read.
The bar source hash is SHA-256 text; the existing certified bar output hash is
canonical decimal UInt64 text for `sum(cityHash64(tuple(*)))`. These distinct
domains are preserved exactly. Feature and coverage content seals use SHA-256
over declared schemas, null positions and canonical Arrow streams of valid
values. Arbitrary hidden bytes beneath nulls and unused bitmap padding are
excluded; every valid Float64 bit, including signed zero, remains significant.

`native_channel_campaign.install` is an explicit producer-only API. Publication
uses `NativeChannelInsertAuthority` under a separate Keeper namespace, reusing
the shared durable dispatch protocol. Unknown INSERTs retain a closed gate;
an explicit trusted original-terminal resolution is required before exact
missing-row resumption. Existing unregistered rows cannot be adopted. Child
readback and actual SSD-part checks precede coverage, and both typed tables
must match before the producer marks its completion fence.
The publisher requires separate read-only and producer transports. The dedicated
`native_causal_channel_reader` has SELECT on three market-source tables, both
feature tables and four storage catalogs. `native_causal_channel_producer` has
INSERT on the two feature tables only. Neither modifies existing Backtest role
grants. The operator command is `python -B scripts/clickhouse/provision_native_channel_principals.py
--apply`, executed on the managed workstation after source synchronization; it
installs only the new product layout and reconciles those two narrow principals.
Private credentials stay in the workstation secret root and are never rotated
or replaced implicitly. The command is a dry run without `--apply`.
`open_native_channel_keeper` authenticates the two feature-specific principals
with digest ACLs. The owner has all namespace permissions; the reader has READ
only. Installation verifies exact private ACLs, never replaces existing ACLs,
and preserves common Keeper paths and other products. READ-only ACL inspection
accepts masked digest hashes by exact principal names and permission bits,
without granting the reader ADMIN.

Installation remains a separate campaign operation: verify schema and
`live_market_ssd` policy, insert immutable child rows, verify actual SSD parts,
publish coverage last, and read back exact typed hashes. A native consumer must
then declare this dependency, obtain read-only grants, and fail preflight on
missing or mismatched coverage before a new numbered strategy can consume it.
`backtest_native_channel_store.read_installed_native_channels` performs SELECTs
only and requires the typed source plan, certified market, SSD placement, exact
rows/coverage hashes and completed producer fence. Its feature read uses the
whole bounded attempt inventory, exposing extra rows rather than filtering
away mismatched identities. The declared table DDL does not itself verify
installation. A bounded August 4, 2026 development pilot for AMIX and ATPC
through 09:30 ET is installed: 309 feature rows and four coverage rows passed
exact native readback, active SSD placement, private Keeper completion and
negative reader/producer permission checks. Its immutable runtime receipt is
`D:\TradingML\runtimes\strategy-optimization-20261005\native_channel_installed_actual_v1\receipt.json`
on the workstation, with a laptop copy named `native-channel-installed-actual-v1.json`.
This is not full-session, full-universe coverage or a financial strategy run.

`load_declared_native_channels` is the SELECT-only dependency loader for an
explicit typed request, feature attempt and declared producer-source hash.
It reconstructs content hashes from installed child/coverage rows, verifies
certified source and SSD placement, and requires that exact reconstructed
projection token to match the completed producer fence. The caller supplies
no feature-content witness. This does not register a native release dependency
or certify a trading population. A future financial strategy must declare
coverage for its causal native candidate population; the hindsight-selected
research cohort below cannot define that population.

`native_channel_qualification.py` supplies the generic declared
`native-completed-channel-qualification@1` rule. Typed bands select relative
channels, native resolutions and finite inclusive bounds; the complete input
policy and bounds are included in the rule payload. The columnar qualifier
intersects with the original mandatory Boolean mask, preserving decision-row
order. Missing, stale, invalid and nonfinite channels cannot qualify; future
or inconsistent availability is rejected. It changes no cash, reservations,
fills or protection and performs no source reads. It must be selected and
bound to installed feature inputs by a future immutable strategy declaration;
adding this helper does not change any existing strategy or publish one.

A development campaign declares 581 packets for 11,547 saved big-move and
negative-control anchors across the 26 PM/AH sessions. Native resolutions are
30 seconds, one minute and five minutes. Completed-anchor clocks use source
midnight milliseconds, `(backtest_anchor_minute + 241) * 60000`. Full source-day
context is retained, but backward as-of alignment selects completed features
only. Research labels remain separate; this cohort is not an executable
opportunity universe or a financial backtest. Immutable packet receipts and
the campaign declaration live under `native_big_move_feature_campaign_v1` in
the existing optimization runtime root. Incomplete packets remain queued;
unknown or failed publication is held for explicit review.

On the workstation, three repeats of the full 330-minute PM decision grid
for six tickers (1,188,000 decisions at 100 ms) used 13,537 actual certified
feature rows. Median feature-alignment times were 4.884, 3.824 and 3.832 seconds
for two-, five- and twenty-minute batches respectively. Observed process RSS
peaks were 143, 212 and 504 MB; maximum output batches were 5.94, 14.86 and
59.44 MB. Every repeat had identical per-resolution availability counts and
zero future selected rows. Five-minute batches are the measured starting
choice for a future consumer, with smaller declared batches available when
memory requires them. Keep batch size separate from decision interval and
source resolution. These measurements include alignment and causal checks,
but exclude strategy state, cash, fills, OCA and journal work: full native
Backtest speed acceptance remains outstanding. Receipts are named
`full-pm-feature-clock-benchmark[-<minutes>min]-v1.json` in the campaign root.

Splits and fundamentals await
verified point-in-time availability; news is omitted. Profitability and complete
session performance require native financial runs, not projection benchmarks.
