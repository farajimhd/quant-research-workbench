# Research and training instructions

- Keep version-specific model, data representation, target construction, losses, metrics, and experimental behavior inside the relevant `research/<family>/vN/` folder.
- Keep only stable cross-version engineering utilities in `research/mlops/` (environment, redaction, manifests, metrics, checkpoints, paths, seeds, profiling, and launch helpers).
- A training-capable version should expose visible config, model/data/loss/metric/train modules, a Python launcher, and a README with purpose, roots, command, and assumptions.
- Historical and live paths must share output schemas and causal/as-of semantics. Preserve identity, availability time, source provenance, and uncertainty.
- Run outputs, checkpoints, W&B files, metrics, manifests, and caches belong under the machine-specific runtime root, never under `research/`.
- Validate compilation, focused tests, deterministic resume behavior, and representative data contracts. Synchronize workstation code only after laptop commit and push.
- Do not call an exploratory model or unvalidated architecture production-ready.
