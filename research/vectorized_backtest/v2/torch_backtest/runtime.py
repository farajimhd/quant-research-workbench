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
    for key, name in (("TORCHINDUCTOR_CACHE_DIR", "inductor"),
                      ("TRITON_CACHE_DIR", "triton"), ("TORCH_EXTENSIONS_DIR", "extensions")):
        target = require_runtime(runtime / name)
        os.environ[key] = str(target)


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
