# Strategy 44 and Torch v2 revision 3

Strategy 44 retains Strategy 43's selected parameters, with revised acquisition
and closeout behavior. Strategy 43 remains immutable. Torch v3 optimization is
outside this change. The app still makes completed 1-second decisions with
native 100ms fills; Torch v2 uses its accepted 1-second execution approximation.

The fixed grid still has 4,320 configurations: 30 entries, M in 5/10/15, three
allocation shapes, two target ladders, two initial stops, two trails and rotation
off/on. Strategy 44 selects signal entry, M=15, inverse-log sizing, structural
targets, confirmed swing stop, adaptive trail and no rotation.

Submit all M fixed-quantity entry parents once, in ascending planned target
order. Reserve their complete acquisition cost and protective fees. Fill nearer
eligible targets to completion before advancing; keep partial remainders at
their original price caps. A completed low at or below the frozen original
setup stop invalidates pending acquisitions across the batch. Rejects, stops,
targets, cancellation and liquidation never unlock the ticker during the session.

Cancel acquisitions five minutes before session end. Start native liquidation
one minute before session end, deferring each leg until a fresh executable quote
exists. Its one-cent floor permits marketable native exits while retaining real
liquidity constraints. Unfilled exits fail terminal qualification. Torch has
explicit 300s entry and 60s liquidation lead Settings with the same ordering.
These are initial defaults, not evidence that every strategy can close.

Partial fills use the app's shared typed full-target profile. OMS creates active
stop/target coverage while attached children remain inactive. Adaptive amendments
align all owned active and attached stops during partial acquisition. The future
parent-fill interval cannot retrospectively trigger its newly activated bracket.

Position presentation groups by acquisition intent, rather than account/ticker.
Resident snapshots capture exact OMS order ownership on the engine boundary;
cold reports join committed command contexts to exact typed intent revisions,
including repair orders and leg-specific liquidation. Each acquired leg has a
stable lifecycle identity, individual quantity, fees and P&L. Broker inventory
and portfolio exposure retain their native aggregated authority. Unfilled
parents are orders, not opened positions. Missing ownership fails closed.

Certified market inputs reuse the existing Strategy 43 ARTE tables and fact IDs;
no new producer, tables or grants are required. Strategy 44 has its own immutable
configuration, source certificate, controller, journal, projector and cold audit.
Older dispatches retain exact reviewed AST projections, including mutation checks.

Publication command (workstation, after verified committed deployment):

```
python -B scripts/clickhouse/publish_strategy_forty_four_configuration.py --deployment-receipt D:/TradingML/runtimes/strategy44/deployments/<commit>/deployment.json --apply
```

The publisher defaults to a read-only plan; --apply publishes and certifies the
new immutable release. The app release remains bounded to September 3, 2026
premarket, 04:00–09:30 America/New_York, $10,000, full certified population.
Live trading and public resume remain unqualified. Wider dates and the revised
full-grid campaign require separate execution and assessment.
