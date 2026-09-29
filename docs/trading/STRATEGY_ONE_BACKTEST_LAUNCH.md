# Strategy 1 Backtest app connection

The intended app topology is a laptop-owned backend and frontend, with direct
network reads/writes to the dedicated ClickHouse principals on the workstation.
`./scripts/services.ps1 start app` selects the local `backend` and `frontend`;
it must not substitute an SSH-forwarded workstation API. ClickHouse is reachable
from the laptop at the workstation HTTP port 18123. The app must never fall back
to SQLite or to a general ClickHouse account.

## Current verified status

On `d3e7a25df`, the Backtest writer publishes a normalized campaign-ownership
checkpoint after each committed completed-market cursor. Its two app-owned
tables are on `live_market_ssd`; the dedicated V4 runner's exact grants and
market-write denial passed workstation preflight. A fresh full-market
2026-08-18 04:00–09:30 ET Strategy 1 run with $100,000 initial cash,
`c925cda1-bd17-490e-ba65-bb13e4d8358f`, passed preflight in 28.280s and
completed in 54.696s. It processed 7,584 persisted-market rows, committed
7,785 normalized journal events in 20 worker units with zero failed units,
and the saved causal audit found 58 intents, 3,682 linked actions, and zero
backdated actions. Three campaign snapshot units used 0.396s total worker
time. The largest observed costs were four compound commit units (19.493s)
and terminal waiting for the prior checkpoint (15.652s); the 25.832s session
stage overlaps asynchronous journal work, so stage totals are not additive.
Keeper logged a connection-drop warning during teardown despite successful
completion and readback. This is one unprofiled observation, not proof of a
speed improvement or checkpoint-resume equivalence. Interrupted-run resume
remains closed pending complete actor restoration.
An unprofiled repeat on `14c0ba065`, run
`8ffb49f1-9155-409c-a8eb-8d9d1586cfa2`, passed 20.207s preflight and
completed in 45.075s with the same 7,584 market rows, 7,785 journal events,
58 intents, 3,682 linked actions, zero backdated actions, and zero failed
writer units. Its 20 writer units used 29.357s of worker time; the three
campaign units used 0.351s. Compound commits used 16.381s and the terminal
phase waited 12.621s for the prior checkpoint. The 9.621s difference from
the first run is uncontrolled run variation, not a measured code speedup.
The laptop app backend was initially stale after these grants changed, and its
old preflight blocked on an unauthorized INSERT grant. Restarting the managed
backend (which also restarts its frontend dependent) loaded the current source.
The full-market Aug 18 HTTP preflight then returned `ready=true`,
`strategy_run_ready=true`, `execution_interval=100ms`, and 18 ready required
checks. POST `/api/trading/backtest/runs` created run
`9d38141a-b352-4605-a61e-2f018d7b88f7`; it reached `completed` after
processing all 7,584 rows with an empty error field. The saved V4 terminal
page returned `market_cursor_verified=true` and no limitations. Managed
backend and frontend status were both `ready` after the run. This verifies
the app API path, not a new browser-visual certification or interrupted resume.
On `89b170d60`, the read-only cold actor audit of this app run's latest
running checkpoint (sequence 7,783) restored campaign ownership together
with the existing broker, OMS, portfolio, manager, and causal-evidence images.
It verified 440 historical fills, zero open broker orders, and zero writes.
The audit is diagnostic: it does not install a resumed controller or certify
post-checkpoint continuation equivalence, so the resume gate remains closed.

New V4 Backtest launches now hold a distinct lifetime Keeper run-owner claim
(`36b2a73f4`) in addition to per-INSERT dispatch fences. The V4 runner and
its bounded detail lanes reject INSERTs after that owner is lost; the writer
checks it before each queued unit and releases it only after draining. This
prevents two new-code processes from concurrently owning the same run ID, but
does not yet authorize interrupted-run resume or retroactively certify older
runs that lacked the claim. The first fresh all-ticker 2026-08-18 04:00–09:30
ET workstation run with this claim, `964f64fe-a974-402a-8550-35745e705f70`,
passed preflight in 25.938s and completed execution in 31.330s, consuming
7,381 persisted rows and committing 2,216 normalized events in 11 units with
zero failed units. Its causal audit found 58 intents, 966 linked actions, and
zero backdated actions. The existing Keeper teardown connection-drop warning
was still logged; completion and audit passed, but the warning is unresolved.

On the current paired evidence/manager restoration code (`8b4e38a4b`), a
fresh workstation 2026-08-18 04:00–09:30 ET all-ticker integration run
`62908518-9fd4-4a8e-8c90-2162ccb237e1` passed preflight in 22.840s and
completed execution in 33.004s. It consumed 7,381 persisted market rows,
committed 2,216 normalized journal events in 11 writer units with zero failed
units, and its causal audit found 58 intents, 966 linked actions, and zero
backdated actions. The measured session phase took 13.985s, including 5.164s
for ten active-ticker first-row reads; terminal confirmation took 11.345s,
including 9.508s waiting for the prior completed-market checkpoint. Sparse
candidate loading took 0.330s. These overlapping stage sums are diagnostic,
not additive wall time. Keeper emitted a connection-drop warning during probe
teardown despite the successful completed run and audit; that warning has not
been resolved. This was a fresh run, not a checkpoint-resume equivalence test.
After bounded concurrent opening of independent active-ticker sources
(`aacef344c`), the same full-market probe
`cc6d9aad-25a9-44ac-a73d-d04d70b3395f` passed a 25.010s preflight and
completed in 32.315s with the same 7,381 rows, 2,216 journal events, 58
intents, 966 linked actions, and zero backdated actions or failed writer
units. Its active first-row stage took 5.376s across ten reads and the
reconciliation stage 5.482s; the 14.913s session and 10.062s terminal stages
were also close to the prior run. This does **not** establish a performance
gain: full-session ticker openings appear mostly isolated, and the 0.689s
total difference is within uncontrolled run variation. The parallel path is
bounded to four readers for simultaneous activations, not a claimed speedup
for this session.
Cold saved-review reads compared this run with the prior `62908518-...` run:
both verified the same terminal market boundary and sequence, final financial
account state, 86 fills, and 86 commissions. Every fill's economic and market
time fields matched after excluding run-scoped record/batch/client-order IDs
and receive timestamps; every commission matched on the same basis. The
terminal wait fences queued normalized V4 event, evidence, manager, broker,
and portfolio units. Dropping those units would weaken restart evidence rather
than optimize the current contract.

On the current V4 running-checkpoint contract, each completed checkpoint now
fences the manager, broker matcher, and a normalized per-account portfolio
recovery snapshot before its asynchronous receipt resolves. The Strategy 1
manager remains bound through the trailing empty market boundary and terminal
cursor. A 2026-08-18 04:00–09:30 ET all-ticker workstation probe on commit
`2fc4f9be9` completed in 27.211s after a 23.511s read-only preflight,
processing 7,381 persisted-market rows. Its writer committed 2,216 normalized
events in nine units, including two running portfolio snapshots, with zero
failed units; the cold causal audit found 58 intents, 966 linked actions, and
no backdated actions. The laptop app then selected that session using its
**exclusive** 2026-08-19 anchor, passed preflight with no blocked checks, and
completed run `b4ef310f-3ccf-47a8-887a-eae9e4f0b3df` about 39s after
creation. The saved V4 terminal page cold-verified sequence 2,216 and its
market cursor with no limitations. Interrupted-run resume remains disabled:
the running captures do not yet constitute a verified complete restore of
controller, OMS, and portfolio state. Two earlier probes during this cutover
failed and remain auditable; they are not successful performance samples.
The cold recovery audit now joins normalized portfolio, manager, broker, and
OMS evidence at one cursor and rejects open broker orders without exact OMS
bindings. A separate SELECT-only broker quote loader uses the certified
`liquidity_100ms_v1` build/attempt and completed bucket, not events or a
current quote. On the completed probe, read-only workstation audits verified
all six pinned quotes at running checkpoint sequence 1,046 (four broker orders
open) and all ten at sequence 2,214. This proves the quote source readback;
it does not restore simulator actors or authorize interrupted-run resume.

Subsequent cold restoration exposed a causal-clock defect in older V4 runs:
three OMS submission paths stamped `submitted_at` with workstation wall time
even in Backtest. The original `ae258af3-b339-49bb-bb3e-709269cd312c`
run remains immutable and is not eligible for OMS actor recovery. Commit
`1388716c7` uses the completed market clock in historical modes and preserves
wall time in live mode. On that code, a new all-ticker 2026-08-18 04:00–09:30
ET run `ff2acf5c-52c1-4c81-8b81-83b1921a9261` completed in 54.989s
with cProfile enabled after a separate 27.597s preflight. A read-only cold
audit restored normalized OMS group state and the complete broker image at
running checkpoint 1,046 (39 fills, four open orders) and checkpoint 2,214
(86 fills, zero open orders). An unprofiled repeat
`eb9c0db5-b038-45ec-b551-abdeb1d6447e` completed in 33.790s after a
separate 28.705s preflight, processing 7,381 persisted-market rows and
committing 2,216 normalized events with zero failed writer units. These are
two observations under different instrumentation, not a controlled speedup.
The OMS image is still a diagnostic reconstruction, not an installed actor;
interrupted-run resume remains disabled pending complete cross-domain
restoration and continuation equivalence.

Normalized causal evidence now has six scalar app-owned ClickHouse tables on
`live_market_ssd`, exact V4 runner grants, and a distinct Keeper-selected
checkpoint head. A first post-cutover run
`7f716f69-59a0-43c3-b550-d2c89701df82` failed closed because the trailing
09:30 scheduler-only boundary advanced the controller but not the evidence
clock; it is not a performance sample. Commit `94a428e73` advances that
explicit empty boundary without creating a bar, quote, or event. A fresh
2026-08-18 04:00–09:30 ET all-ticker run
`7fb329b2-8b46-4fa1-b106-e106233d1e8e` completed in 32.061s after
25.374s preflight, with 7,381 market rows, 2,216 normalized journal events,
zero failed writer units, and no backdated actions. The writer published two
evidence snapshots; SELECT-only cold audits read and verified their normalized
children at sequences 1,046 and 2,214, including the selected Keeper head
at the latter checkpoint. A subsequent SELECT-only audit constructed a fresh
causal-evidence actor from the saved V7 seed token and certified ARTE
candidate, pivot, HOD, and interval products. It restored the normalized
evidence state and matched an immediate recapture at both checkpoints,
without writes. Future execution after restart has not been compared with
the uninterrupted run, so interrupted-run resume stays disabled.

The next cold portfolio audit found that historical fill allocations used the
workstation wall clock. Commit `13788b4d2` changes allocations and related
portfolio freshness timestamps to the injected completed-boundary clock; 197
focused and replay tests plus eight subtests passed. On this code, a fresh
unprofiled 2026-08-18 04:00–09:30 ET all-ticker run
`10432d8a-3cb3-45e9-95b0-3ee1cacb1098` completed in 28.764s after a
separate 23.238s preflight, processed 7,381 persisted rows, and committed
2,216 normalized journal events with zero failed units and zero backdated
actions. SELECT-only cold audits passed at checkpoint 1,046 (39 fills, four
open orders) and checkpoint 2,214 (86 fills, zero open orders), including
portfolio, manager, OMS, and broker reconstruction. These audit results do
not yet prove actor installation or resumed-run equivalence; the resume gate
remains closed.

The laptop-managed app is running the current backend and frontend. A direct
HTTP preflight for the entire 2026-08-18 04:00–09:30 ET session returned
`strategy_run_ready=true`, `execution_interval=100ms`, and 18 ready required
checks. A laptop HTTP launch created run
`c206cc66-334b-44bd-9692-a08f3193db80`; polling remained available through
asynchronous checkpoint persistence, then reported `completed` after about
61 seconds from creation (7,584 processed persisted-market rows). Its typed
ClickHouse journal cold-verified against workstation baseline
`aee645c9-d9c9-4252-948c-a49280395044`: the terminal account, 58 strategy
intents, 440 executions and commissions, and 30 order commands matched.
The app lists the run as reviewable; its terminal page has a verified market
cursor and no limitations. No run-local Backtest directory was created.

The saved WFF chart endpoint returned persisted bars and indicators at 100 ms,
1 s, 5 s, 10 s, and 30 s. Daily and monthly context reads succeeded, and a
requested missing indicator appeared in `unavailable_columns`. These are API
and data-contract checks, not approval of every Canvas visual state; the user
will review the certified Canvas presentation separately. The laptop app's
first preflight after restart took about 27 seconds and a warm repeat about
5 seconds. A workstation in-process full-session execution at the same
current contract took 50.141 seconds after preflight. These timings are
observations, not a throughput guarantee; older measurements below belong to
earlier market, broker, and journal contracts and must not be compared as a
controlled speedup.

Strategy 1 remains Backtest-only. Live admission is fail-closed until
server-enforced prior-writer fencing and complete normalized cold recovery
are implemented and validated. Interrupted Backtest resume is also disabled
before any legacy SQLite read; do not describe a successful completed run as
checkpoint-resume validation. Candidate 350 event-native re-entry remains
outside Strategy 1's certified fixed-bar behavior.

The managed local backend reads the dedicated V3 market reader, V4 runner,
and journal credential files over the protected workstation share. It copies
no credential into the repository or a laptop artifact. All three principals
were verified from the laptop with read-only `SELECT 1` on 2026-09-28.

**Keeper cutover:** the user selected a secured LAN Keeper endpoint. The client
requires an explicit private IPv4 endpoint, CA certificate, and client
certificate/key; it enables TLS, peer verification, and hostname verification.
The laptop client key and workstation CA/server keys remain on their origin
hosts. The managed ClickHouse start performs a certificate preflight before
stopping the server, installs the strict-TLS listener on 9281, preserves the
block on plaintext 9181, and publishes a Windows forward restricted to laptop
192.168.1.99. The server certificate names workstation 192.168.1.218. If
either address changes, update the managed settings and reissue the certificate
before restart; never bypass verification or widen the firewall rule.

The managed listener is running on the workstation. On 2026-09-28, a laptop
Kazoo session verified the Keeper peer certificate, opened a writable session,
and read the root children. The workstation's plaintext LAN Keeper port 9181
remains blocked. The laptop backend restores Python's standard SSL context at
startup if `pip-system-certs` has replaced it: the injected wrapper broke
Kazoo's TLS socket, while the standard context still verifies the pinned CA
and server hostname.

The laptop-hosted backend and frontend were restarted with the secured Keeper
settings. A local app-route preflight passed for all 6,100 Aug 18 tickers and
100 ms evaluation in 24.985s, with no blocked checks. A subsequent local
`POST /api/trading/backtest/runs` completed run
`cccd2d2b-1211-415b-9477-d0fb73eff35a` in 45.800s from creation to the
terminal update, processing 7,381 persisted liquidity rows. The causal
session took 19.261s; final journal durability took 15.378s. The bounded
asynchronous ClickHouse writer committed 2,216 normalized events in seven
units with zero failed units. A separate saved terminal page returned completed
status and a verified market cursor. Its WFF saved-chart request returned
1,000 persisted 1-second bars and 1,000 closed MACD rows in 0.731s with a
warm certificate cache. These laptop-to-workstation timings are measured
observations, not a throughput guarantee. A cold saved-chart certificate read
took about 40s in a standalone check, and one concurrent cold API request hit
the 60s ClickHouse timeout; cold chart latency still needs optimization.

A later clean laptop backend restart returned the same saved chart in 9.380s
cold and 0.682s warm; two simultaneous cold app requests both returned HTTP
200 in 10.7s. The earlier 60s timeout was not reproducible under this bounded
concurrency check, so it remains an observed load-dependent failure, not a
proven steady-state latency. The browser-tested Strategy 1 recent-runs path
now uses the dedicated V4 credential file and skips legacy disk inventories:
32 typed ClickHouse runs loaded in 0.418s, newest run first. Previously the
page omitted all V4 runs and enumerated 1,105 legacy files. Its saved WFF
chart loaded persisted bars and closed MACD in the laptop UI without a page
error. Journal-table CSS no longer constrains the chart library's internal
table: at 1440px, the plotting cell measured 918px rather than 260px.
Light/default and dark/1.25-scale browser checks found no horizontal page
overflow; timeframe labels now use the theme foreground color. The frontend
production build and opt-in saved-review browser tests passed. These checks
do not imply that every chart state or theme/scale combination was exercised.

Saved-run evidence pages can be read directly from ClickHouse without starting
a new Backtest.
The workstation-backend and SSH-API procedure used in the historical tests
below was a validation setup, not the app deployment contract.

## Measured full-market premarket validation

On 2026-09-28, the synced branch at `7ac7162c1` repeated the full Aug 18
04:00–09:30 ET all-ticker probe. Read-only preflight took 27.418s with no
unresolved checks; execution took 29.234s and processed 7,381 persisted
liquidity rows. Run `22e2fe67-076c-43e4-ba9a-7375a532183f` completed with
2,216 normalized journal events, seven committed writer units, zero writer
failures, 58 intents, 966 linked actions, and no backdated descendants.
Stage timing was 14.435s for the causal session, 6.496s for terminal
durability, 3.601s for ten active-ticker first rows, and 3.723s for twenty
reconciliation calls. Keeper logged connection-drop/retry messages during
the run and shutdown; the run completed and the cold causal audit passed,
but the messages still merit operational diagnosis. This controller-path
probe does not constitute a visual browser QA of the saved review/chart.

In earlier app-route validation on Strategy 1 code hash
`6760ec7e70322d34955c17acbd34b24e8b1b26a2597d8a70ec5cb3777fdcc565`,
two independent workstation app-route runs of the entire Aug 18 04:00–09:30 ET
premarket completed in 29.612s and 29.834s of execution after 23.559s and
20.844s cold preflight. Runs `fa99ad4f-1a49-47f7-8d25-8a747997a1bf` and
`ccdb626d-bece-4154-b7a0-3c5b216e5dba` each processed 7,381 persisted
liquidity rows and drained 2,217 normalized journal events with zero failed
units. Fresh read-only V4 verification found matching run/definition/code
hashes, final account state, all 58 portfolio decisions and strategy intents,
86 executions and commissions, and 30 order commands. The later run's
29.834s profile spent 15.959s in the session phase and 7.358s in terminal
durability fencing; sparse loading took 0.185s. Keeper connection-drop
messages appeared during process shutdown after completed verification and
remain a separate diagnostic. A comparison with older run
`8f5bbb79-eecd-4e01-a59f-b01952b4d870` is not same-code equivalence:
its recorded code hash differs, and its portfolio-decision rows differ even
though its final account, fills, fees, intents, and commands match.

On 2026-09-28, the workstation's certified ARTE build
`1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5`
passed read-only preflight for all 6,100 Aug 19 tickers. With Strategy 1's
completed-second V7 geometry cache, full 04:00–09:30 ET Backtests completed:

| Session | Cold preflight | Backtest execution | Persisted liquidity rows | Run ID |
|---|---:|---:|---:|---|
| 2026-08-19 | 23.755s | 32.053s | 6,809 | `5116ec93-bc36-454c-bd49-d56b8254e1c3` |
| 2026-08-18 | 24.867s | 32.816s | 7,381 | `daaaacd4-4723-4318-bf9f-e86e4cf4dae0` |

The Aug 19 pre-change run `b05c309d-f2c7-401e-9819-99cd9721775e`
took 38.787s of execution on the same full session. A cold, read-only V4
journal comparison confirmed matching final account state, 45 portfolio
decisions, 45 strategy intents, 97 executions, 97 commissions, and 39 order
commands before and after the optimization. The optimized runs' asynchronous
ClickHouse journal queues drained with zero failed units; neither produced a
run-local directory. These are observed workstation timings, not a throughput
guarantee. Live Strategy 1 order admission and Candidate 350 re-entry parity
remain separate acceptance gates.

On 2026-09-28, after adding a normalized V4 command-lineage child and
provisioning its exact Backtest and Live grants, the app route repeated the
entire Aug 18 premarket for all tradable tickers. Run
`798b6ed5-16f4-4e80-8ece-a810d89ae9cb` passed preflight in 22.718s and
completed execution in 30.266s, processing 7,381 persisted liquidity rows.
The journal committed 2,217 events in 10 units with zero failed units. A
separate read-only cold verification reached sequence 2,217 and reconstructed
all 30 order commands from normalized typed intent, Portfolio, OMS, and
lineage rows. The V4 journal layout check verified all 16 tables and SSD
placement after the run. These observations do not grant Live order admission;
broker reconciliation and executable OMS restoration remain separate gates.

An additional workstation app-API run selected the full Aug 18 session by
passing the exclusive `anchor_date=2026-08-19`. Its preflight returned
`strategy_run_ready=true`, `execution_interval=100ms`, zero blocked checks,
and the same certified market token above. POST `/api/trading/backtest/runs`
created run `75ad9d96-8a92-4eed-9b7b-de89c4b5a097`; it completed in
32.233s from creation to terminal update, processing 7,381 liquidity rows.
The writer drained 10 units and 2,217 event rows with zero failures. A fresh
`v4-terminal-page` read verified sequence 2,217, its market cursor, and the
terminal account state with no limitations. The run created no directory under
the workstation Backtest runtime root. The test backend and SSH tunnel
were stopped afterward. This validates the app routes, not visual browser
interaction or live trading.

On 2026-09-28, a separate backend on workstation loopback port 8001 using the
synced source opened that saved V4 run in the actual Backtest UI. The WFF chart
loaded 1,000 persisted 1-second bars and 1,000 closed MACD rows through the
verified boundary. Targeted browser review passed at 1440×900/dark/100% and
1024×768/light/125%, with no layout redesign or objective UI issues. The
temporary backend, tunnels, and frontend were stopped; the pre-existing
workstation backend on port 8000 was left running. This verifies cold saved
review and chart rendering, not live order admission.

A read-only full-market Aug 18 preflight profile on the workstation ran twice
in one process and once in a fresh process. With profiling overhead, the two
cold calls took 32.113s and 33.178s; the in-process warm call took 3.883s.
Both cold profiles included a Keeper connection drop during Kazoo session
shutdown, with 11.615s and 12.592s spent in `ManagedKeeperSession.close`.
These profiled values are not the uninstrumented app latency above. The
probe used plan-only mode and inserted no market or journal rows. The
profiled Keeper close cost motivated a process-local, read-only Keeper
transport in commit `70d0a0d51`. It retains no certificate or lease, rereads
the attestation on every preflight, and retires disconnected sessions only
after their current readers exit. A fresh-process, unprofiled plan-only check
with that code took 19.987s cold; a separate profiled check took 33.634s cold
and 3.335s warm. These are observations under different instrumentation and
load, not a controlled claim of seconds saved. Kazoo still logs the connection
drop while closing at process exit; the Backtest request no longer waits for
that per-preflight close. A later unprofiled run measured Keeper close at
0.105s; the profiled cumulative shutdown time is not evidence of a practical
Keeper bottleneck.

After syncing that source and restarting the workstation backend, the actual
`POST /api/trading/historical-preflight` route returned `ready=true` with no
blocked checks for the full Aug 18 04:00–09:30 ET market. The first request
took 23.202s and the identical warm request took 2.640s. These are route
latencies, not Backtest execution times. The backend remained healthy on
port 8000 after both requests; no Backtest run was created.

A further unprofiled full-market Aug 18 run, using the same 100ms contract,
completed execution in 31.818s after a 23.143s cold preflight. Run
`b6c36e59-fd62-4a65-9c66-a047490f38b9` processed 7,381 persisted
liquidity rows. Its measured terminal journal confirmation took 7.814s,
while writer close took 0.001s and Keeper close took 0.105s. The asynchronous
writer committed 10 units and 2,217 event rows with zero failures. A fresh
`v4-terminal-page` request independently confirmed completed status, verified
sequence 2,217 and market cursor, a flat account, and no review limitations.

Read-only sparse-market profiles on that certified Aug 18 build fetched the
same 62,072 candidate rows for 957 tickers from 200 bounded shards at each
worker setting. The exact-key fetch took 6.849s with 4 workers, 3.753s with
8, and 3.125s with 16. The Backtest's sparse-read ceiling is now 16, but it
opens no more lanes than the vectorized static gate leaves surviving shards.
This raw-source profile does not include that gate, preflight, brokerage, or
journal completion.

The first end-to-end run after the ceiling change, run
`dcc439aa-4113-4005-ae9e-d06d4ccc0812`, took 32.081s of execution.
Its actual post-gate sparse load was only 0.189s, so the worker increase did
not measurably accelerate this session relative to the preceding 31.818s run.
Both runs cold-verified 2,217 committed journal events with identical category
counts and final account balances, no open positions, and no review limitations.
The adaptive-pool full-market rerun `ed91a04a-0954-4d96-80cb-b95e7bccd704`
completed in 31.964s after a 24.040s cold preflight, with 7,381 processed
liquidity rows and a 0.198s sparse load. Its cold terminal page independently
verified sequence 2,217, the market cursor, the same $10,297.95 flat account,
and no limitations. The three unprofiled runs cluster near 32s; the sparse
worker ceiling is not the dominant bottleneck for this session.

Terminal-phase instrumentation on another full-market Aug 18 run
(`8f5bbb79-eecd-4e01-a59f-b01952b4d870`) measured 32.561s execution
and 7.532s terminal confirmation. Of that terminal time, 5.433s fenced
the prior completed-market cursor and its queued typed journal/snapshot work,
2.091s awaited the final ClickHouse account-snapshot receipt, and runtime
finish/capture took under 0.01s. The writer committed 10 units and 2,217
events with zero failures. A fresh terminal-page read verified the complete
sequence and cursor, the same flat account balance, and no limitations. This
wait is a durability/causality fence after the engine has advanced, not
SQLite or run-local disk I/O on the hot path.
The same run's read-only commit-header profile found six V4 commits for all
2,217 events, with no singleton commit. The two manager and two broker-match
snapshot units are separately attested recovery state; removing them merely
to lower the measured wall time would weaken the current journal contract.

On 2026-09-28, a current-code full-market Aug 18 rerun took 21.400s for
read-only preflight and 32.673s for execution (`96c23b93-1de5-4ad2-8f3b-abb4860f8c80`).
It processed 7,381 persisted liquidity rows, committed 2,217 normalized
journal events in 10 units with no failed units, and its causal journal audit
found 58 intents, 966 linked actions, and no backdated actions. Execution
stage timing attributed 17.684s to the causal session and 7.890s to the
terminal durability fence. A separate cProfile-instrumented rerun
(`9b161ea5-746d-4b82-a74f-81af8d1b253e`) took 53.834s; that instrumented
wall time is not comparable to the unprofiled runtime. Neither run generated
a run-local journal directory. The preflight profile separately measured
34.340s with instrumentation, including exact `bars_v1` and
`liquidity_100ms_v1` integrity reads; its plan-only mode created no run.

After bounded 256-row active-ticker read-ahead removed a worker-thread hop at
each in-memory 100 ms boundary, two full-market Aug 18 reruns took 26.354s and
25.980s of unprofiled execution, after 19.753s and 19.985s cold preflight.
Runs `3e682b11-be35-439c-b77b-454957da43a3` and
`2c4502b4-e8f2-4b25-a5e4-a5c4a943ca05` each processed 7,381 persisted
liquidity rows, with zero failed journal units or causal audit violations.
The second run measured 13.060s in the session and 6.412s at the terminal
durability fence; its scheduler took 1.370s over 7,498 boundaries. These
times are observed workstation measurements, not a guaranteed speedup. Cold
V4 comparison found identical final account, 58 portfolio decisions and
intents, 86 executions and commissions, 30 order commands, and every journal
event-kind count between the two optimized runs. Relative to the earlier
`96c23b93-1de5-4ad2-8f3b-abb4860f8c80` run, all compared financial and
order outcomes match. Its one additional event was a periodic
`checkpoint/market_boundary`, not a strategy or broker action. The current
larger costs are active-ticker first-row/reconciliation reads and the final
ClickHouse durability fence; no source-table write or run-local journal was
introduced by read-ahead.

The same read-ahead code also completed the full Aug 19 premarket twice:
`d99a0a45-061d-42ea-86ee-647c004ddfa7` in 28.725s and
`92149cc7-88c6-446a-95b3-8867f7c7a431` in 30.365s, after 27.333s and
24.319s cold preflight. Each processed 6,809 persisted liquidity rows and
passed the causal audit with 45 intents and no backdated descendants. Cold
comparison matched the final account, all 45 portfolio decisions and intents,
97 executions and commissions, 39 order commands, and journal event-kind
counts. The older pre-read-ahead run `5116ec93-bc36-454c-bd49-d56b8254e1c3`
had matching financial outcomes but a different code hash and `decided_at`
values; it is not same-code replay equality. Aug 19's active-ticker first-row
reads took 5.5–5.7s and terminal durability about 6.2–6.4s, now larger than
its 0.46–0.48s scheduler cost.

A separate full-market Aug 18 app-route profile used the UI's $100,000 cash
setting rather than the $10,000 integration-probe setting above. Two runs at
the original 1,024-record Backtest checkpoint threshold completed in 74.485s
and 66.434s of execution after 26.959s and 23.957s preflight. Run IDs were
`880efba4-0661-4cb5-b2bb-7dba055107a8` and
`1a46d058-fa93-4f46-b2d2-51cf4a06db97`. Each committed 7,785 normalized
journal events in 17 writer units with zero failed units and three sets of
manager, broker, and evidence snapshots. Their causal session phases took
24.898s and 23.302s, while terminal phases took 42.116s and 36.207s,
mostly awaiting prior journal commits. A cold causal audit of the first run
found 58 intents, 3,682 linked actions, and zero backdated actions. The
larger event volume makes these timings incomparable to the $10,000 runs.

A bounded trial doubling only the Backtest checkpoint threshold to 2,048
records did not reduce the snapshot count or terminal backlog. On that code,
run `5ac2dcda-8085-4eab-8221-b86221362e46` completed in 68.991s after
21.641s preflight, with the same 7,785 events, 17 writer units, and three
snapshot sets; its terminal phase took 39.470s. The threshold was restored
to 1,024 rather than retaining a longer unrecoverable suffix without measured
benefit. The next performance target is normalized snapshot publication and
readback, not a looser recovery cadence. All three runs completed despite
Keeper connection-drop/retry messages during shutdown; those messages remain
an operational issue, not a claimed clean bill of health.

A later instrumented app-route run confirmed the same 7,785-event shape, but
its global Python call profile could not isolate the journal worker from other
threads, so its 118.344s wall time is **not** an optimization comparison.
The misleading profiler was removed. Source inspection identifies a concrete
repeated-work path: each manager, broker-match, and evidence publication calls
`load_verified_v4_prefix`, a cold verifier that rereads and rehashes every
committed batch and normalized detail row. Those snapshots are published
three times in the $100,000 run. Any warm-path optimization must retain the
Keeper-compacted prefix, exact current-batch row hashes, causal cursor, and
cold reader verification; merely omitting the readback would weaken the
journal contract.

On commit `396f46747`, writer-owned Strategy 1 snapshot publication switched
from re-verifying all older V4 detail batches to checking the current
Keeper-compacted head and re-verifying the current batch's normalized detail
rows. Cold review and recovery still use the full-prefix scan. The full
Aug 18 app-route run at $100,000 cash (`cbb8e513-41a5-4cca-b7a6-7ced45e96b4b`)
passed preflight in 27.254s and completed execution in 58.773s. It kept the
same 7,785 journal rows, 17 writer units, three snapshot sets, and zero
failed units. The three snapshot families together took 13.687s, versus
roughly 27–30s in the two comparable earlier runs; terminal handling took
23.002s versus 36–42s. The session itself took 27.812s, so this is a
measured reduction in snapshot/terminal cost, not a claimed strategy-engine
speedup. A fresh cold causal audit verified all 7,785 events, 58 intents,
3,682 linked actions, and zero backdated actions. Keeper connection-drop
messages still appeared during shutdown and remain unresolved.

Commit `7d79b8681` reused the first current-batch detail verification for
later snapshot families at the *same* Keeper head, with lease, gate, and
stored-commit-hash checks on each reuse. A same-configuration full Aug 18
app-route run (`25461a1e-52a5-4419-866e-deaa9944c4cc`) completed in
58.986s after 29.323s preflight, with 7,785 events, 17 writer units, three
snapshot sets, and zero failures. Snapshot-family time fell further to
8.971s, and terminal handling to 20.969s. Overall execution was essentially
unchanged from the prior 58.773s run because the causal session took 29.409s
instead of 27.812s; no end-to-end speedup is claimed for this one sample.
A fresh cold audit verified 58 intents, 3,682 linked actions, and zero
backdated actions. The Backtest-specific resume HTTP route now rejects before
the generic disk-manifest/SQLite Replay path; actor restoration remains
fail-closed rather than pretending a saved Backtest is resumable.

Commit `b1621e513` cached immutable typed-journal schema shapes and Decimal
bounds during row canonicalization. On the same full-market Aug 18 app route
with $100,000 cash, run `b7b31eac-ab6b-4b5d-9c60-e0b2fc49ec3f` passed
preflight in 26.438s and completed in 56.005s. It committed 7,785 events
in 17 writer units with zero failures; cold review audited 58 intents,
3,682 linked actions, and zero backdated actions. Compound preparation took
7.589s versus 8.554s in the immediately preceding run. This single
measurement suggests lower CPU preparation cost, but does not establish a
stable end-to-end speedup. Session and terminal phases took 27.715s and
19.889s. Keeper shutdown connection warnings remained.

Commit `8225619b9` made the warm writer snapshot cache enforce the caller's
commit-count bound. A fresh full-market Aug 18 04:00–09:30 ET app-route run
at $100,000 cash (`aa37b71d-458a-446d-9236-9692cfe3e31b`) passed
preflight in 24.100s with no blocked checks and completed execution in
52.942s. It processed 7,584 strategy rows and committed 7,785 journal
events in 17 writer units with zero failed units. Session evaluation took
25.976s; terminal handling took 18.959s, of which 14.169s was prior-commit
work. The separate cold causal audit verified 58 intents, 3,682 linked
actions, and zero backdated actions. This is another single-run measurement,
not proof of a stable improvement. Keeper connection-drop/retry warnings
still appeared at shutdown; interrupted-run resume remains disabled.

A read-only repeated full-market preflight on the same workstation process
measured 22.114s cold and 3.000s warm (Aug 18, $100,000, 100ms); it created
no run or journal. A separate CPU-instrumented preflight took 33.493s cold
and 3.862s warm. Its cold cumulative profile attributed 10.808s to
certified market-plan discovery, including session-seal reads, and 4.826s
to market-plan coverage verification; instrumentation and concurrent threads
make those cumulative figures unsuitable for addition into wall time. The
existing in-process validated-plan reuse is material; cold discovery is the
next preflight optimization target, without skipping coverage or weakening
the fail-closed launch gate.

After the reusable completed-MACD rule began rejecting lossy clocks in
`a1c243f1e`, a fresh full-market Aug 18 app-route Backtest
(`19b07c08-d021-4fc4-b9a0-d405455c88ca`) passed preflight in 23.878s
and completed execution in 44.980s at $100,000 cash. It processed 7,584
strategy rows and committed 7,785 normalized events in 17 writer units,
with zero failed units. A separate cold causal audit matched the prior
58 intents and 3,682 linked actions with zero backdated actions. The session
took 22.917s and terminal handling 14.935s, including 11.014s waiting for
the prior commit. This is one faster measurement under variable service
latency, not evidence that the clock guard itself accelerated execution.
Keeper shutdown connection warnings remain.

On September 29, a fresh read-only workstation preflight for the full
August 18 market measured 22.524s cold and 2.445s warm in one process.
This confirms that repeated experiments mostly avoid the cold certificate
scan; it created no run or journal. A separate instrumented full-session
run (`6a5040f7-2646-4bb5-8893-163372432195`) completed with 7,584 rows,
7,785 events, and zero failed writer units. Profiling raised its execution
wall time to 84.872s, so that figure is not a production-speed sample. The
four compound commits spent 15.654s in preparation and 18.476s in
publication; the terminal waited 22.379s for a prior checkpoint commit.

A bounded 2,048-record flush-threshold trial (`764ae99d3`) passed the
56 focused controller tests and completed an unprofiled full-session run
(`7593c9a4-883e-43f5-a147-192f5102e360`) in 46.904s after 20.488s
preflight. It still emitted four compound batches and three snapshot sets,
with 15.156s terminal prior-commit wait, and did not improve on the earlier
45.075s measured run. The threshold was restored to 1,024 in `e92d14aee`
and synchronized to the workstation. Further optimization should target
compound preparation/readback or snapshot publication directly, preserving
the exact journal prefix and causal recovery anchor.

Commit `f56cb7b42` removed a second typed-row seal during V4 compound
child comparison while retaining the first full seal, exact scalar equality,
and committed ClickHouse readback. It passed 143 focused journal tests and
an unprofiled full-session Aug 18 run
(`ea543ebe-e636-4371-b00c-65d7aa5612bf`): 20.613s preflight,
45.844s execution, 7,584 processed rows, 7,785 events, 20 writer units,
zero failed units, and zero backdated actions. Compound preparation took
5.968s and publication 10.674s. This single run does not prove a wall-time
speedup; network insertion and readback remain the larger measured cost.
