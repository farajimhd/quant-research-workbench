from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from research.mlops.paths import MLOpsPathConfig


SECRET_KEY_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def discover_env_files(repo_root: Path, explicit: str | Path | None = None) -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    for raw in os.environ.get("DOTENV_PATHS", "").split(os.pathsep):
        if raw.strip():
            paths.append(Path(raw.strip()))
    paths.append(repo_root / ".env")
    ml_root = MLOpsPathConfig.from_env().ml_root
    paths.append(ml_root / ".env")
    paths.append(ml_root / "secrets" / ".env")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_env_files(paths: Iterable[Path], *, verbose: bool = True) -> list[Path]:
    loaded: list[Path] = []
    for path in paths:
        if path.exists():
            load_env_file(path)
            loaded.append(path)
    if verbose:
        if loaded:
            print("Loaded .env files: " + "; ".join(str(path) for path in loaded), flush=True)
        else:
            print("No .env file found in discovered locations.", flush=True)
    return loaded


def load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in SECRET_KEY_PARTS)


def secret_status(keys: Iterable[str]) -> dict[str, str]:
    return {key: "present" if os.environ.get(key) else "missing" for key in keys}


def redact_mapping(values: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in values.items():
        if is_secret_key(key):
            redacted[key] = "present" if value else "missing"
        else:
            redacted[key] = value
    return redacted
