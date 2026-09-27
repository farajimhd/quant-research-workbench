# Applications

Planned applications: live runtime, data maintenance, backtest worker, control/observer
API, and copied minimal frontend. Process boundaries are defined in the
[architecture](../doc/02-architecture.md).

Copy selected frontend source into this area before modifying it. No application
may invoke the parent backend or frontend.

`arte-cli` implements offline help/version, bounded market-event replay, and
`plan-historical-update`. The planner reads a bounded JSON request on stdin
and emits a hashed stage plan. It does not download files, connect to
ClickHouse, run a historical update, or certify data. The actual updater and
flatfile importer are not implemented yet.
It is not a complete strategy backtest. Service commands fail before starting
processes or opening sockets.
