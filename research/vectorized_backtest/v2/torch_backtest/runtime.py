"""Runtime authority and deterministic artifact sealing."""
from pathlib import Path
from hashlib import sha256
import json
import os

ROOT = Path("D:/TradingML/runtimes")
DEFAULT = ROOT / "vectorized_backtest" / "torch_backtest_v2"


def require_runtime(path):
    root, target = ROOT.resolve(), Path(path).resolve()
    if not root.is_dir() or not target.is_relative_to(root) or target == root:
        raise ValueError("Required output is beneath D:/TradingML/runtimes; no alternate root")
    target.mkdir(parents=True, exist_ok=True)
    return target


def configure_caches(runtime=DEFAULT):
    runtime = require_runtime(runtime)
    configure_compiler()
    for key, name in (("TORCHINDUCTOR_CACHE_DIR", "inductor"),
                      ("TRITON_CACHE_DIR", "triton"), ("TORCH_EXTENSIONS_DIR", "extensions")):
        target = require_runtime(runtime / name)
        os.environ[key] = str(target)


def configure_compiler():
    """Use the pinned v2-only Windows compiler when the base environment lacks it."""
    import importlib.util
    import sys
    target = DEFAULT / "dependencies" / "triton-3.7.1.post27"
    if importlib.util.find_spec("triton") is None and (target / "triton").is_dir():
        import torch
        if not torch.__version__.startswith("2.12."):
            raise RuntimeError("V2 Triton 3.7 overlay requires PyTorch 2.12; no version fallback")
        sys.path.insert(0, str(target))
        os.environ["PYTHONPATH"] = str(target) + os.pathsep + os.environ.get("PYTHONPATH", "")


def write_json(path, value):
    path = Path(path)
    require_runtime(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def file_hash(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def code_hash():
    # All v2 source, including copied adapters, belongs to one integrity seal.
    base = Path(__file__).parent
    h = sha256()
    for path in sorted(base.rglob("*.py")):
        h.update(path.relative_to(base).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def source_revision(repo):
    """Support an isolated committed workstation payload without borrowing a checkout."""
    import subprocess
    marker = DEFAULT / "deployments" / Path(repo).name / "deployment.json"
    if marker.exists():
        value = json.loads(marker.read_text())
        if value["v2_code_hash"] != code_hash():
            raise ValueError("Workstation payload differs from its verified deployment")
        return value["commit"]
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
