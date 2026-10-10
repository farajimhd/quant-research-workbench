"""Runtime authority and deterministic artifact sealing."""

import json
import os
from hashlib import sha256
from pathlib import Path

ROOT = Path("D:/TradingML/runtimes")
DEFAULT = ROOT / "vectorized_backtest" / "torch_backtest_v6"


def require_runtime(path):
    root, target = ROOT.resolve(), Path(path).resolve()
    if not root.is_dir() or not target.is_relative_to(root) or target == root:
        raise ValueError(
            "Required output is beneath D:/TradingML/runtimes; no alternate root"
        )
    target.mkdir(parents=True, exist_ok=True)
    return target


def specialization_budget(population,batch_size,generations,sessions):
    """Finite frame budget: one specialization per scheduled batch/session.

    One extra generation envelope covers reconstruction/qualification priming.
    Round to a power of two, with a fixed ceiling; never allow unlimited guards.
    """
    if any(type(v) is not int or v<1 for v in (population,batch_size,generations,sessions)):
        raise ValueError('Positive explicit compiler budget dimensions required')
    maximum=((population+batch_size-1)//batch_size)*(generations+1)*sessions
    limit=max(128,1<<(maximum-1).bit_length())
    if limit>16384:raise ValueError('Compiler specialization budget exceeds bounded capacity')
    return limit


def configure_caches(runtime=DEFAULT,*,recompile_limit=128):
    if type(recompile_limit) is not int or not 1<=recompile_limit<=16384:
        raise ValueError('Bounded explicit compiler specialization limit required')
    runtime = require_runtime(runtime)
    configure_compiler()
    import torch
    # Exact lot capacities and uniform-mode branches are deliberate, bounded
    # V5 variants. Exhaustion remains an error (fullgraph), never an eager fallback.
    torch._dynamo.config.recompile_limit=recompile_limit
    torch._dynamo.config.accumulated_recompile_limit=max(256,recompile_limit)
    import tempfile

    scratch = require_runtime(runtime / "compiler-tmp")
    os.environ["TMP"] = os.environ["TEMP"] = str(scratch)
    tempfile.tempdir = str(scratch)
    for key, name in (
        ("TORCHINDUCTOR_CACHE_DIR", "inductor"),
        ("TRITON_CACHE_DIR", "triton"),
        ("TORCH_EXTENSIONS_DIR", "extensions"),
    ):
        target = require_runtime(runtime / name)
        os.environ[key] = str(target)


def configure_compiler():
    """Use the pinned v3-only Windows compiler when the base environment lacks it."""
    import importlib.util
    import sys

    # Toolchain reuse is read-only; V4 source/runtime remain independent. This
    # is an installed compiler package, not a V3 strategy implementation.
    target = ROOT / 'vectorized_backtest' / 'torch_backtest_v3' / "dependencies" / "triton-3.7.1.post27"
    if importlib.util.find_spec("triton") is None and (target / "triton").is_dir():
        import torch

        if not torch.__version__.startswith("2.12."):
            raise RuntimeError(
                "V3 Triton 3.7 overlay requires PyTorch 2.12; no version fallback"
            )
        sys.path.insert(0, str(target))
        os.environ["PYTHONPATH"] = (
            str(target) + os.pathsep + os.environ.get("PYTHONPATH", "")
        )
        compiler = target / "triton" / "runtime" / "tcc" / "tcc.exe"
        if not compiler.is_file():
            raise RuntimeError("Pinned Windows Triton bundled C compiler is missing")
        # The wheel normally locates TinyCC under sysconfig's site-packages.
        # A --target install requires this explicit task-local toolchain path.
        os.environ.setdefault("CC", str(compiler))
        # CPU custom-op dispatch can probe availability before this overlay is
        # installed. Refresh discovery after changing the search path; do not
        # retain a cached "unavailable" result for the now-configured package.
        from torch.utils import _triton
        for probe in vars(_triton).values():
            clear=getattr(probe,'cache_clear',None)
            if callable(clear):clear()


def write_json(path, value):
    path = Path(path)
    require_runtime(path.parent)
    from time import sleep
    from uuid import uuid4

    # Independent writers never share a temporary pathname. Windows/SMB
    # readers may briefly deny delete-sharing while reading the old snapshot;
    # retry that atomic replacement, NEVER publish a partially written JSON.
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8"
    )
    for attempt in range(12):
        try:
            temporary.replace(path)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32) or attempt == 11:
                raise
            sleep(min(0.02 * 2**attempt, 0.2))


def file_hash(path):
    h = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def code_hash():
    # All v3 source, including copied adapters, belongs to one integrity seal.
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
        if value["v5_code_hash"] != code_hash():
            raise ValueError("Workstation payload differs from its verified deployment")
        return value["commit"]
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
