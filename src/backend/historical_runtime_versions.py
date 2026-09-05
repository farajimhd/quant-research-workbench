"""Reject new backtests against stale executors without rewriting saved runs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.trading_runtime.strategy_engine import STRATEGY_ID, STRATEGY_REVISION

ROOT = Path(__file__).resolve().parents[2]


def _fingerprint(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\n")
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
        digest.update(b"\0")
    return digest.hexdigest()


def qmd_source_fingerprint() -> str:
    services = ROOT / "services"
    files = [services / "qmd_history_gateway" / "build.rs"]
    for name in ("qmd-gateway", "qmd_history_gateway"):
        files.extend((services / name / "src").rglob("*.rs"))
        files.extend(path for filename in ("Cargo.toml", "Cargo.lock")
                     if (path := services / name / filename).is_file())
    return _fingerprint(services, files)


def backend_source_fingerprint() -> str:
    return _fingerprint(ROOT, [* (ROOT / "src/backend").rglob("*.py"),
                               * (ROOT / "src/trading_runtime").rglob("*.py")])


LOADED_BACKEND_FINGERPRINT = backend_source_fingerprint()


def runtime_version_check(configuration: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    problems = []
    strategy = dict(configuration.get("strategy") or {})
    selected = [(strategy.get("strategy_id"), strategy.get("revision"))]
    selected.extend((row.get("strategy_id"), row.get("strategy_revision"))
                    for row in configuration.get("assignments") or [])
    for strategy_id, revision in selected:
        if strategy_id == STRATEGY_ID and int(revision or 0) != STRATEGY_REVISION:
            problems.append(f"Selected Long Momentum revision {revision}; latest is {STRATEGY_REVISION}. "
                            "Refresh the strategy profile and create/select a new test candidate.")
    expected_qmd = qmd_source_fingerprint()
    if health.get("source_fingerprint") != expected_qmd:
        problems.append("QMD History does not match workspace source. Rebuild/restart qmd-history with scripts/services.ps1.")
    if backend_source_fingerprint() != LOADED_BACKEND_FINGERPRINT:
        problems.append("Backend source changed after startup. Restart backend with scripts/services.ps1.")
    return {"id": "runtime_versions", "label": "Current execution code and strategy",
            "status": "blocked" if problems else "ready", "required": True,
            "summary": " ".join(dict.fromkeys(problems)) if problems else "Running executors match workspace source and selected strategy is current.",
            "evidence": {"strategy_revision": strategy.get("revision"),
                         "latest_strategy_revision": STRATEGY_REVISION,
                         "qmd_source_fingerprint": health.get("source_fingerprint"),
                         "expected_qmd_source_fingerprint": expected_qmd,
                         "backend_source_fingerprint": LOADED_BACKEND_FINGERPRINT}}
