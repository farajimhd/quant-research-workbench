# Validation

In-process Rust tests live alongside the implemented modules. Frozen reference-only
source snapshots and their hash manifest live in `reference/`. Do not start test
services before the user copies ARTE to the new repository.

Organize future tests by contracts, source identity, V7 parity, replay, recovery,
portfolio/OMS, broker simulation, resource isolation, UI contracts, and extraction.
The [validation design](../doc/07-backtest-validation.md) defines acceptance gates.

Tests must run without parent source or services. Copy small approved fixtures or
provision them through this project's own test tooling. Never require a live account
for ordinary tests. Store generated reports and large recordings outside source.
