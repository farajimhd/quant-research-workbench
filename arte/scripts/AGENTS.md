# Release and lifecycle entry points

Read `../doc/10-deployment.md` and `../doc/08-performance-operations.md`.

- Maintain one deploy entry point and one run entry point. Select an explicit
  laptop or workstation profile; inspect the host to validate, not silently
  rewrite, its approved CPU, RAM, queue, storage, or concurrency budgets.
- Package only ARTE-owned source/build output and pinned external dependencies.
  Record source-copy provenance and component hashes. Never package secrets,
  parent paths, parent launchers, caches, or generated logs in a release.
- Install immutable versioned releases. Compare component and configuration
  fingerprints before restarting; leave compatible services running. Disarm,
  drain, reconcile, and verify broker protection before any live replacement.
- Preserve durable maintenance cursors and checkpoint/schema compatibility on
  upgrade or rollback. Deployment never automatically arms Live.
- Until the user copies ARTE to its new repository, scripts may perform only
  builds, static checks, offline unit tests, and release packaging. Keep
  service start, network tests, broker login, migrations, and connected probes
  disabled. Do not bypass this gate for a smoke test.
