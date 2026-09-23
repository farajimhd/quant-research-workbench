# Broker protocol and account safety

Read `../../../../doc/06-trading-broker.md` and verify current broker primary
documentation before changing a protocol assumption.

- Keep Live and Paper sessions, account allowlists, permissions, and credentials
  distinct. Backtest and Shadow may not acquire broker submit capability.
- Serialize unresolved order-confirmation chains per brokerage session. Apply
  reviewed notification suppression only to explicitly approved categories;
  unknown prompts block rather than disappear. Preserve order, fill, and
  connection updates.
- Coordinate endpoint/session pacing across accounts; reserve capacity for
  protection and reconciliation. Unknown submission status requires broker
  reconciliation before retry, never blind resubmission.
- Reject every exposure increase without a broker-supported stop and profit
  target covering partial and full fills. During applicable regular hours,
  require fresh official LULD bands and buffered directional geometry. Do not
  silently shift a stop or target to make an order pass.
- Persist the authorized command and reservation before submission. Reconcile
  broker fills, positions, child activation, and protection after disconnect
  and restart. One account's failure must not silently undo another's result.
- Do not claim bracket safety from locally constructed payloads or paper-only
  tests. Connected broker acceptance remains deferred until extraction.
