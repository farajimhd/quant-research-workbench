# References and provenance

## Status

The design records user decisions through 2026-09-15. External facts below were
reviewed during the design discussion. Recheck provider and broker contracts before
implementation release. Account entitlements and live payloads still require testing.

## External specifications

| Source | Used for |
|---|---|
| [Massive REST trades](https://www.massive.com/docs/rest/stocks/trades-quotes/trades) | Trade identity, sequence, participant/SIP clocks and pagination |
| [Massive REST quotes](https://massive.com/docs/rest/stocks/trades-quotes/quotes) | Quote clocks, sequence and pagination |
| [Massive streaming trades](https://www.massive.com/docs/websocket/stocks/trades) | Live trade fields and timestamp precision |
| [Massive streaming quotes](https://massive.com/docs/websocket/stocks/quotes) | Live quote fields and available clocks |
| [Massive streaming overview](https://www.massive.com/docs/websocket/stocks/overview) | Channels, including LULD |
| [ClickHouse MergeTree](https://github.com/ClickHouse/ClickHouse/blob/master/docs/en/engines/table-engines/mergetree-family/mergetree.md) | Partition and sorting keys; no dense ordinal requirement |
| [IBKR Web API](https://ibkrcampus.com/campus/ibkr-api-page/webapi-doc/) | Order replies and session-scoped suppression |
| [IBKR submit new order](https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-orders/submit-new-order) | Account-specific POST endpoint and orders object; checked during request-boundary implementation |
| [IBKR brokerage session status](https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-session/get-brokerage-status) | Authentication-status endpoint, established/connected/competing flags and response wrapper |
| [IBKR bracket orders](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/bracket-orders-oca-groups) | Bracket request relationships; exact broker protection still needs acceptance tests |
| [IBKR pacing](https://www.interactivebrokers.com/docs/web-api/v1/pacing-limitations) | Global and endpoint request limits; 15-minute penalty reviewed during transport pacing implementation |
| [IBKR account discovery](https://ibkrcampus.com/docs/web-api/v1/endpoints/accounts/receive-brokerage-accounts) | Tradeable account discovery |
| [IBKR Paper](https://ibkrcampus.com/campus/glossary-terms/paper-trading-account/) | Paper behavior and execution-model limitations |

## Source-copy inventory policy

Phase 0 copied no application source. The initial Rust implementation now includes
eleven frozen reference-only text snapshots under `tests/reference/`. Their origin
manifest records source hashes. They are not executable packages or runtime imports.
The implementation must inventory only the components actually selected for copying.
Required categories are market normalization, bars, indicators, historical/streaming
V7, detectors, selected strategy, portfolio, OMS, broker support, reference support,
and the reduced frontend.

The temporary source repository was inspected at commit
`7c660cf7aa70b3d5917891e3b282acfffabbe604` during scaffold preparation.
This is provenance, not a required runtime revision or a strategy approval.

Copied code must be readable and testable without the old source tree. Public source
links and origin hashes are allowed as provenance. File imports, service calls,
build paths and test prerequisites back into the parent tree are prohibited.
