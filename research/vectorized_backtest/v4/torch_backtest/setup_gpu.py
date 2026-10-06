"""Explicitly install the pinned compiler into v3 runtime, not a shared environment."""
import os
import sys
from pathlib import Path
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import subprocess
from uuid import uuid4
import torch
from research.vectorized_backtest.v4.torch_backtest.runtime import (
    DEFAULT, require_runtime, configure_compiler)


def main():
    if os.name != "nt" or not torch.__version__.startswith("2.12."):
        raise RuntimeError("This setup targets Windows PyTorch 2.12 with Triton 3.7 only")
    base = require_runtime(DEFAULT / "dependencies")
    target = base / "triton-3.7.1.post27"
    if not target.exists():
        stage = require_runtime(base / ("install-" + uuid4().hex))
        subprocess.run([sys.executable, "-B", "-m", "pip", "install", "--target", str(stage),
            "--only-binary=:all:", "--no-deps", "--no-compile", "--index-url", "https://pypi.org/simple",
            "--cache-dir", str(require_runtime(base / "pip-cache")), "--report", str(stage / "installation.json"),
            "triton-windows==3.7.1.post27"], check=True)
        stage.rename(target)
    configure_compiler()
    import triton
    if triton.__version__ != "3.7.1":
        raise RuntimeError("Pinned compiler version differs from installation")
    print(f"V3 compiler ready: Triton {triton.__version__} at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
