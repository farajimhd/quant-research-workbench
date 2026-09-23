# Historical REST acquisition and gap repair

Read `../../../../doc/03-events.md` and `../../../../doc/04-data-lifecycle.md`.
This also governs the flat `rest_acquisition.rs` module via the parent
`src/AGENTS.md` ownership map.

- Fetch historical trades and quotes only through bounded REST workers.
  Resolve point-in-time instrument identity and paginate to completion,
  including equal-timestamp page boundaries. Keep retry cursors durable and
  restart-safe; a transport error is not an empty certified interval.
- Normalize into the same event contract used by WebSocket. Preserve source
  clocks, precision, conditions, corrections, and REST provenance. Do not
  synthesize live receive or execution time from SIP or acquisition time.
- Insert idempotently by verified source identity. Certify source coverage
  only after counts, ordering, capabilities, conflict resolution, and exact
  readback have passed. Report which checks ran; do not claim absolute
  completeness without provider evidence.
- Repair missing declared source and derived dependencies before trading
  readiness. Preserve live receipt metadata across REST overlap. Rebuild
  bars, indicators, Boolean products, and V7 dependencies from the preceding
  certified state; catch-up must not submit expired historical orders.
- Reconcile the fully closed session before historical V7 seed publication.
  A later correction publishes a successor generation and cannot hot-swap
  the active session's seed.
