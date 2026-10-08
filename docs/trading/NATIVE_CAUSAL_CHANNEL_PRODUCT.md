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
installation. No feature product has yet been installed by this implementation.
Splits and fundamentals await
verified point-in-time availability; news is omitted. Profitability and complete
session performance require native financial runs, not projection benchmarks.
