# ARTE

Automated Real-Time Trading Engine.

Status: design and folder scaffold. No runtime is implemented here yet.

ARTE runs live strategies on a workstation. It also supports isolated
historical backtests and an optional browser interface. Its market-data component
is named Market Data Engine (MDE). The repository and project folder are named `arte`.

Start with the [design index](doc/README.md). Read the
[project charter](doc/01-charter.md) before implementation.

## Independence

This folder is the complete project boundary. Its temporary parent repository
is not a build, test, deployment, or runtime dependency. Copying this folder to
a new repository must preserve every project-relative path.

Existing parent files remain unchanged. Selected source may be copied into this
folder, reviewed, and adapted here. The frontend follows the same rule.

ClickHouse is the only external persistence service. Market-provider and broker
connections remain external integrations. Required supporting programs must be
included or provisioned by this project's own distribution.

## Layout

| Folder | Purpose |
|---|---|
| `doc/` | Requirements, architecture, contracts, and acceptance gates |
| `crates/` | Shared Rust domain libraries |
| `apps/` | Executables and the copied, reduced frontend |
| `adapters/` | Provider, broker, reference, and bundled helper integrations |
| `config/` | Versioned configuration contracts and device profiles |
| `database/` | New-database schema and migration sources |
| `scripts/` | Project-owned build, deploy, run, and validation tools |
| `tests/` | Contract, parity, recovery, load, and isolation tests |

These folders contain responsibility descriptions, not placeholder services.
There are no runnable deployment commands or executable configuration examples
until the implementation gates are satisfied.

## Current delivery

- Agreed design recorded on 2026-09-15.
- No application source copied yet.
- No database, service, account, or workstation changed.
- No strategy performance or live-trading approval implied.
