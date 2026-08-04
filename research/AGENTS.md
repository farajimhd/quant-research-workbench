# Research and training instructions

- The workstation heavy-training root is `\\DESKTOP-SAAI85T\Workstation-D\TradingML`, with configured SSD/HDD data locations. The laptop repository remains the source of truth; workstation runtime folders are never authoritative.
- Keep version-specific model, data representation, target construction, losses, metrics, and experimental behavior inside the relevant `research/<family>/vN/` folder.
- Keep only stable cross-version engineering utilities in `research/mlops/` (environment, redaction, manifests, metrics, checkpoints, paths, seeds, profiling, and launch helpers).
- A training-capable version should normally expose `config.py`, `model.py`, `data.py`, `losses.py` or `objectives.py`, `metrics.py` when version-specific, `train.py`, Python launchers such as `run_train.py`, separate job launchers when needed, useful notebooks, and a README with purpose, roots, command, and assumptions.
- Runtime scripts should prefer Python launchers over PowerShell-only workflows. A launcher must run directly, print the equivalent command, allow simple overrides, resolve laptop/workstation roots, and use clear unbuffered progress output.
- Historical and live paths must share output schemas and causal/as-of semantics. Preserve identity, availability time, source provenance, and uncertainty.
- Run outputs, checkpoints, W&B files, metrics, manifests, and caches belong under one run directory per job beneath the machine-specific runtime root, never under `research/`.
- Workstation copies must be self-contained, include required version/shared `mlops`/package files, resolve workstation data and runtime roots, and be hash/contents verified after sync. Sync only after laptop commit and push.
- Every run manifest should record model family/version, job, run identity, git commit, arguments, resolved data/output roots, checkpoints, W&B identity, and secret presence only (never secret values).
- Never copy `.env` files into runtimes, checkpoints, notebooks, W&B files, logs, or manifests. Load secrets from environment/configured discovery and redact `*_KEY`, `*_TOKEN`, `*_SECRET`, and `*_PASSWORD` values.
- Validate compilation, focused tests, deterministic resume behavior, representative data contracts, and the isolated workstation launcher when workstation training is affected. Metrics are evidence, not production certification.
- Do not call an exploratory model or unvalidated architecture production-ready.
