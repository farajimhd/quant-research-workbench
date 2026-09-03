#!/usr/bin/env python3
"""Launch the native historical structure-checkpoint campaign on Windows.

The campaign executable owns calculation, checkpointing, retries, and its
stable terminal dashboard. This launcher only resolves or builds that binary;
it never implements a second level-book algorithm.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "services" / "qmd_history_gateway" / "Cargo.toml"
BINARY_NAME = "structure_checkpoint_campaign.exe" if os.name == "nt" else "structure_checkpoint_campaign"


def binary_candidates(explicit: str | None, environ: dict[str, str]) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if configured := environ.get("QMD_STRUCTURE_CAMPAIGN_BINARY"):
        candidates.append(Path(configured).expanduser())
    runtime_root = Path(environ.get("TRADING_RUNTIME_ROOT", r"D:\TradingML\runtimes"))
    candidates.extend(
        (
            runtime_root / "bin" / BINARY_NAME,
            runtime_root / "cargo-target" / "quant-research-workbench" / "release" / BINARY_NAME,
            MANIFEST.parent / "target" / "release" / BINARY_NAME,
        )
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def resolve_cargo(environ: dict[str, str]) -> str | None:
    if cargo := shutil.which("cargo"):
        return cargo
    user_profile = environ.get("USERPROFILE")
    if user_profile:
        candidate = Path(user_profile) / ".cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_binary(
    explicit: str | None,
    *,
    build_if_missing: bool,
    environ: dict[str, str],
) -> Path:
    candidates = binary_candidates(explicit, environ)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if not build_if_missing:
        searched = "\n  ".join(str(path) for path in candidates)
        raise RuntimeError(f"campaign binary was not found; searched:\n  {searched}")
    cargo = resolve_cargo(environ)
    if cargo is None:
        raise RuntimeError(
            "campaign binary is missing and Cargo was not found. Copy the prebuilt binary to "
            r"D:\TradingML\runtimes\bin\structure_checkpoint_campaign.exe or set "
            "QMD_STRUCTURE_CAMPAIGN_BINARY."
        )
    subprocess.run(
        [cargo, "build", "--release", "--bin", "structure_checkpoint_campaign", "--manifest-path", str(MANIFEST)],
        cwd=REPO_ROOT,
        env=environ,
        check=True,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Cargo completed, but the campaign binary was not found in the configured runtime target")


def parse_launcher_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--launcher-help", action="store_true")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    launcher, campaign_args = parse_launcher_args(list(sys.argv[1:] if argv is None else argv))
    if launcher.launcher_help:
        print("Launcher options: --binary PATH, --no-build, --launcher-help")
        print("All other options are forwarded to structure-checkpoint-campaign v3.")
        return 0
    environ = dict(os.environ)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        binary = resolve_binary(
            launcher.binary,
            build_if_missing=not launcher.no_build,
            environ=environ,
        )
        return subprocess.run([str(binary), *campaign_args], env=environ, check=False).returncode
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Unable to launch structure checkpoint campaign: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
