# Performance, resources, and operational state

## Hardware profiles

The run entry point selects a named `laptop` or `workstation` profile.
Profiles provide fixed approved budgets. Device inspection validates the profile;
it does not silently rewrite the requested values.

Required settings include live cores, ticker shards, execution reserve, memory budget,
queue capacities, maintenance workers/RAM, backtest CPU/RAM, ClickHouse query limits,
and process priority. A profile that exceeds the device fails validation.
Do not invent numerical production budgets before measurement.

## Resident state

Keep the configured live universe's current-session events, required history,
bars, indicators, level state, and strategy inputs resident.
Size the working set before arming. Include quotes, fitting observations, correction
history, live buffers, and peak bursts. No silent event truncation is allowed.
If the full requested universe cannot fit, reject that profile or request an explicit
scope change. Do not quietly fall back to per-decision ClickHouse reads.

## Concurrency

Use ticker ownership to avoid broad mutable shared locks. Keep causal updates serial
within the owning lane. Parallelize independent ticker calculations and fitting work
where the algorithm permits it. Apply results only at their correct input boundary.

Use contiguous arrays and vectorized operations where they reduce measured cost.
Do not delay a live event to fill a large SIMD batch. Do not use all cores if that
starves ingestion, the account coordinator, broker communication, or the OS.

Bound every worker pool, queue, batch, and cache. Measure p50/p95/p99/p99.9 and maxima.
Publish event rates and sample counts with latency percentiles.

## Latency audit

Stamp receipt at the earliest application receive boundary before parsing/batching.
Keep UTC for source comparisons and monotonic time for internal elapsed durations.
Record clock synchronization quality and uncertainty.

| Measurement | Meaning |
|---|---|
| Participant to SIP | Source reporting delay, when both timestamps exist |
| SIP to receive | Observed feed age, including clock uncertainty |
| Receive to state update | Local queue and computation delay |
| State update to decision | Strategy delay |
| Decision to submission | Portfolio, validation, durability and adapter delay |
| Submission to acknowledgment | Broker/network acknowledgment delay |

Do not attribute every source-to-receive delay to the provider. Distinguish stale
reports, network/feed age, local backlog, and clock error. Quote participant time
may be absent live. A quiet instrument is not stale merely because it has no trades;
evaluate connection health and the freshness of operands actually needed to trade.

## Incident policy

States: healthy, warning, exposure-blocked, recovering. Thresholds and recovery
hysteresis are mandatory approved settings. Undefined thresholds prevent Live arming.

- Alert on meaningful state changes.
- Repeat unresolved actionable alerts at a bounded configured interval.
- Show affected instruments, metrics, oldest queue age, and blocked reasons.
- Block new exposure on stale required inputs, unreliable clocks, or stale decisions.
- Keep protective and reduce-only workflows available.
- Resume only after coverage, freshness, and broker reconciliation recover.

Re-evaluate all required operand ages before submission, not only at signal creation.
Do not execute a queued signal after its deadline.

## Isolation and contention

Backtest and maintenance use separate processes and output namespaces. They have no
live broker credentials. They cannot exhaust Live's reserved CPU or memory.
Limit shared ClickHouse queries and I/O as well as worker counts.
Throttle or pause background work when Live breaches its resource budget.
Stronger isolation may require another host; process separation alone is insufficient.

## Durable observability

Report service version, readiness, ownership, source frontier, persistence backlog,
repair progress, seed identity, account reconciliation, and protection state.
RUNNING or HTTP 200 alone does not establish readiness.
Secrets and authentication payloads never enter logs, manifests, or UI output.
