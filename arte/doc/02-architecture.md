# Architecture and authorities

## Process boundaries

| Process | Owns | Must not do |
|---|---|---|
| Live runtime | MDE, ticker state, streaming V7, strategy, portfolio, OMS | Wait for chart rendering or fetch features through HTTP |
| Data Maintenance | REST acquisition, repair, certification, historical V7, retention | Submit broker orders or rewrite active live state |
| Backtest worker | Replay and simulated execution | Connect to a live broker or mutate live namespaces |
| Control and observer API | Commands, cached status, audit queries, UI delivery | Become market or trading authority |
| Broker support | Packaged gateway/authentication lifecycle | Infer account permission from UI selection |
| Reference service | Identity, sessions, splits, tick rules and broker mappings | Depend on the parent reference service |

Required support processes belong to this distribution. Their exact language is
not a contract. The latency-critical domain code is Rust.

## Live data path

```text
WebSocket -> receive timestamp -> normalize -> ticker-owned state
                                              |
                           bars -> indicators -> streaming V7/detectors
                                              |
                                           strategy
                                              |
                                   account cash reservation
                                              |
                                    bracket/risk validation
                                              |
                              durable command record -> broker
```

Market persistence and observer delivery branch from this path through bounded
queues. They do not run as synchronous UI work inside a ticker update.
The durable pre-submit command record is a deliberate persistence boundary.
It is not a market-feature query.

Use owned state, safe references, and immutable snapshots. Do not pass raw memory
addresses across process boundaries. Preserve event order within each ticker.
Account-wide cash reservations serialize across competing ticker intents.

## Dependency plan

Each strategy declares its instruments or causal admission rule, event channels,
bar intervals, indicators, lookbacks, V7 version, detector inputs, references,
and source capabilities. The planner merges identical requirements.

Discovery needed for admissions is included only when declared. It does not
require the parent scanner. Newly admitted instruments cannot trade until warm.
Lookback calculations include required pre-admission history.

The shared `dependency_plan` contract represents each requested instrument,
dependency and half-open interval explicitly. Definitions pin implementation hashes
and declare their input dependencies and extra history. The planner propagates
those requirements through the dependency graph. It merges overlapping or adjacent
intervals, preserves gaps, and emits dependency-first work with strategy consumers.
It does not create a cross product of all instruments and all dependencies.

Missing definitions, cycles, unpinned implementations, invalid clocks and capacity
violations reject the plan. Equivalent input ordering produces the same plan hash.
The plan describes required work; it is not coverage evidence or permission to trade.
Current declarations are caller-supplied. The `startup_repair` adapter binds trade
and quote requirements to provider authorities and verified acquisition coverage.
It creates maintenance jobs only for missing intervals. Source revision, channel,
instrument and certificate publication time must match the check. Each job uses
the existing resumable acquisition contract and has a deterministic ownership key.
Non-event dependencies remain explicitly unresolved in the report.

Exporting the effective strategy contract, checking derived coverage, and scheduling
the complete repair and warming sequence remain integration work. An empty source
job list does not mean that the strategy is ready.

## Startup state machine

`STOPPED -> VALIDATING -> RECEIVING_BUFFERED -> REPAIRING -> WARMING -> READY`

- VALIDATING checks configuration, resources, storage, versions, and account mode.
- RECEIVING_BUFFERED captures live arrivals before the historical handover.
- REPAIRING certifies the missing source and derived intervals.
- WARMING loads the certified historical seed and replays the causal prefix.
- READY requires reconciled broker state and fresh required feeds.

Readiness is per instrument and per account, with a global operator gate.
A strategy with cross-instrument requirements waits for its entire dependency set.
No exposure increase is allowed while a required dependency is unready.

## Shutdown and recovery

Disarm new exposure first. Drain commands and reconcile ambiguous broker results.
Do not cancel valid protective orders merely because a process is stopping.
Flush market persistence and capture the final durable input cursor.
Leave broker-held protection in place. Record any incomplete durability range.

Only one designated Live owner may control an account set. ClickHouse is not a
distributed lease service. Failover requires old-owner fencing and reconciliation.
The laptop test runtime is not a second live owner.
