# Historical Backtest reference identity

Market preparation owns the session population decision: an exact certified
Reference Gateway snapshot, or an explicitly approved carried-forward snapshot.
Broker identity preparation must follow that same snapshot, never independently
choose today's universe or require an unrelated exact-day legacy publication.

`historical_reference_identity.load_reference_pin` verifies the market build's
selected-session scope against its Keeper receipt. Publication and V2 Backtest
certification share this resolver. The publisher reads the pinned immutable V2
population, verifies its original ClickHouse population hash, and joins only the
retained source publication by ticker, symbol/listing/security identity, source
run and capture time. Missing, duplicate, changed or late facts fail closed.

The linked `arte.strategy_one_identity_v2` and
`arte.strategy_one_identity_coverage_v2` tables add broker IDs without mutating
market products or reference snapshots. Their coverage includes snapshot ID,
reference revision, original population hash, availability and cutoff clocks,
source universe date, and identity/reference hashes. Both tables require
`live_market_ssd`, including actual active-part placement.

The old snapshot hash did **not** include broker conids. This migration certifies
the retained broker values now, matched to historical identity evidence; it does
not claim the original population hash authenticated those conids. Source values
are re-read before coverage publication. A changed retained source blocks reruns.
Carry-forward remains explicitly labeled and may omit subsequent listings or
include listings that stopped trading after its original capture.

Existing V1 publications keep their exact tokens for saved-run compatibility.
If no V1 publication exists, Backtest requires V2; a corrupt or ambiguous V1
publication never silently falls back. New publisher executions write only V2.
Partial V2 children are invisible until exact readback succeeds and the single
coverage row is inserted. Reruns verify completed coverage; they do not overwrite
it. Producer concurrency for a given build/session must remain serialized.

Deployment order: reconcile the candidate producer's exact derived-table grants
and install the two tables, reconcile the Backtest V3 reader grants, then publish
identities with the existing `publish_strategy_one_identities.py` command pinned
to the requested session and build. Backtest has no reference or market writes.
Use the app-route preflight after publication; identity readiness alone does not
prove pivot, HOD, execution-price, V7 or entry-evidence coverage.
