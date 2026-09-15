# Integration adapters

Own the Massive REST/WebSocket, ClickHouse, IBKR, reference-data and authentication
support integrations. Bundle required helper runtimes through this project's release.

Adapters implement domain interfaces. They do not define strategy rules or depend
on a pre-existing parent service.

`arte-adapters` contains Massive normalization and REST paging, ClickHouse storage
checks/inserts, and IBKR payload/reply interpretation. Only pure in-process tests
have run. WebSocket transport and full broker lifecycle remain unimplemented.
