# Distribution, deployment, and repository extraction

## Project identity

The product is ARTE (Automated Real-Time Trading Engine).
The project folder and future repository are named `arte`.
Use the `arte-` prefix for future project-owned packages and executables.
Keep MDE as the market-data component name within ARTE.
These names do not introduce a parent-repository dependency.

## Portable root

All source, tests, configuration schemas, migration definitions, and documentation
live inside this project. Use project-relative paths within source. Never resolve
the temporary parent directory as a workspace dependency.

Do not use parent Cargo workspaces, Python modules, node_modules, environment files,
launchers, service registries, ports, databases, caches, or frontend proxies.
No symlink or junction may point back to the parent tree.

The full deployment contracts below remain the target. Offline validation and CLI
packaging entry points exist. Full service deployment is not yet implemented.
See [implementation status](14-implementation-status.md).

## Release contents

- Live, maintenance, backtest, and control/observer binaries.
- Built frontend assets from this project's copied source.
- Required broker gateway, reference components, and authentication helpers.
- Pinned language runtimes for any retained non-Rust helper.
- Lockfiles, schema versions, configuration schemas, and license notices.
- Release manifest with component hashes and compatibility constraints.

Third-party packaging must comply with its license. Where redistribution is not
permitted, the project-owned installer provisions a pinned approved dependency.
It must not discover a working copy inside the parent app.

## Entry points

One deploy entry point installs a specified immutable release for a target device.
One run entry point reconciles desired service state using the named device profile.
The run entry point also owns status and graceful stop operations.

Deploy validates signatures/hashes and schema compatibility, installs a versioned
directory, preserves the preceding release, and updates release metadata atomically.
It does not automatically arm trading or interrupt unrelated running services.

Run compares each component's binary, configuration and schema fingerprints.
Leave compatible unchanged services running. Restart changed services and any
dependents whose contracts require it. Show the proposed restart set first.
A code change to the live monolith requires its coordinated restart; an observer-only
change does not. A maintenance update must preserve its durable job cursor.

If accounts have open exposure, live replacement requires disarm, command draining,
broker reconciliation, verified protection, and an explicit permitted restart policy.
Do not kill and relaunch the process without this handover.

## Device profiles

Provide separate laptop and workstation profiles with explicit CPU, memory, ports,
concurrency, retention and runtime-root settings. Secrets are external references.
Validate the host against the selected profile; do not silently resize or redirect it.
Only the designated host/account profile may arm Live.

Runtime output lives outside source: binaries, logs, recordings, caches and reports.
ClickHouse remains the durable state authority. Local prepared caches are disposable.
Require explicit code-install, runtime-output and secret locations during setup.
Do not embed the temporary parent repository's absolute paths in release files.

## Rollback

Retain the prior release and component manifests. Rollback must check schema and
checkpoint compatibility. A binary rollback cannot silently roll back a database.
Use restart-safe migrations and forward-compatible readers where required.
Never delete or rewrite a source generation to make an older binary start.

## Copy provenance

Before copying application code, record source repository identity, commit, relative
path, SHA-256, destination, license, and adaptation notes. The origin manifest is
historical provenance, not an executable dependency or runtime file locator.
Copy algorithm tests and minimal fixtures where licensing and storage rules allow.
Remove parent imports and service calls before accepting a copied component.

## Extraction gate

After the initial implementation, copy this root into an empty independent checkout.
Build, test, install, run maintenance, replay a fixture, and serve the UI there.
The parent tree must be absent or inaccessible. No parent processes may be running.
Provision only declared external integrations and the new ClickHouse database.

The latest user restriction changes the order: the user copies ARTE into its new
repository before any service-backed testing. Builds and in-process tests may run
before that move. Run the connected extraction gate only afterward. Repository
creation and service activation remain separate user-controlled actions.
