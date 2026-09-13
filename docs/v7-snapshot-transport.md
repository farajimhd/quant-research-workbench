# V7 replay snapshot transport

QMD remains the owner of the streaming MLE engine. Replay requests exact
completed-second cutoffs; it does not construct levels or advance the engine
locally.

Strategy cursors use `v7-snapshot-delta-1` on the existing QMD snapshot endpoint.
The first response contains every level once. Subsequent responses contain changed
levels, removals, level ordering and changed metadata. The backend reconstructs
the original snapshot, including both level-array aliases, all fit diagnostics,
timestamps and source/checkpoint provenance. Chart requests retain the existing
full snapshot/segment contract.

Each packet names its base version. A missing server base (including worker
restart or eviction) returns a complete replacement. The decoder rejects an
unexpected delta base. QMD freezes the previous transport state so subsequent
engine mutations cannot change it. The worker retains at most 16 transport bases,
matching its default engine-session capacity.

Within a replay run, frame and event consumers share one V7 cursor per ticker.
The cursor retains at most 32 exact-second snapshots and checks the returned
cutoff and maximum input timestamp. A request for an older cached second returns
that snapshot, never the latest frame snapshot. If evicted, the existing QMD
causal rewind path reconstructs that prefix. Non-V7 cursors remain independent.
Callers treat returned snapshots as read-only.

The `structure_snapshot` performance stage measures QMD retrieval and cursor
cache access, including thread dispatch. It is inclusive when called from a
larger measured execution stage. Compare runs with identical saved configuration,
run plan, cash, source authority and session; candidate IDs alone are insufficient.

Deploy by rebuilding/restarting QMD History and its managed backend dependent
through `scripts/services.ps1`. The strategy behavior and historical checkpoint
format are unchanged; no checkpoint campaign rebuild is required.

Validation on September 13, 2026: 200 Python tests passed (two optional tests
skipped), and the managed QMD History release build passed. An alternating
40-second SUGP comparison reconstructed identical full snapshots: median payload
519,863 to 7,352 bytes, median request plus decode 21.96 to 14.57 ms.
The August 21 04:00–04:30 Strategy 197 comparison preserved all 76 fills,
22 stop replacements and three target replacements, including their event times
and trading values. Runtime was 370.48 to 224.14 seconds, but the original also
spent 37.22 seconds on chart presentation polling while the comparison was
headless. This is not an isolated end-to-end speed claim. Reports and reproducer
scripts are under `D:/TradingML/runtimes/v7-delta-validation`.
