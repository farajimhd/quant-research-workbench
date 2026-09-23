# Live WebSocket and in-process MDE path

Read `../../../../doc/02-architecture.md`, `../../../../doc/03-events.md`,
and `../../../../doc/08-performance-operations.md`. This also governs the
flat `live_market.rs` module via the parent `src/AGENTS.md` ownership map.

- Timestamp receipt before parsing. Preserve SIP/participant/receive clocks
  separately and track clock uncertainty. A delayed trade or quote is not
  timely because it has a recent processing timestamp.
- Normalize and apply each instrument's events in order to resident state.
  Share owned market state with bars, indicators, signals, V7, and strategy;
  do not request decision features through HTTP or ClickHouse.
- Define every executable calculation's own event or fixed execution interval.
  Complete 100 ms bars and one-second V7 inputs only at their causal ends.
  Do not delay an event merely to fill a vector batch.
- Branch compact-event persistence and observer frames through bounded queues.
  Accepted events cannot be silently dropped. Persistence saturation disarms
  new exposure and leaves an explicit unresolved range.
- Measure source-to-receive and receive-to-decision latency separately.
  Connection health, actual operand freshness, backlog, and clock quality
  determine exposure readiness. A quiet ticker alone is not a stale feed.
- Startup buffers live arrivals while maintenance repairs certified gaps and
  warms required state. Reconcile overlap, broker state, and protection before
  arming. Recovery must preserve exact cursors and must not trade the catch-up
  prefix as if it were current.
