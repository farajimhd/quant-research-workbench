"""Deploy committed, pushed source to a new isolated workstation directory.

No secrets or original Torch version are copied. Shared certification readers
and their source packages are included; deployment evidence stays in runtimes.
"""
import os
import sys
from pathlib import Path
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[4]
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO))

import argparse
from hashlib import sha256
import json
import subprocess
from uuid import uuid4
from zipfile import ZipFile
from research.vectorized_backtest.v2.torch_backtest.runtime import DEFAULT, require_runtime

SHARE = Path("//DESKTOP-SAAI85T/Workstation-D/TradingML")
PAYLOAD = ("src", "pipelines/market_sip/events", "research/mlops", "research/vectorized_backtest/v2")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit", default="HEAD")
    args = p.parse_args(argv)
    commit = subprocess.check_output(["git", "rev-parse", args.commit], cwd=REPO, text=True).strip()
    upstream = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], cwd=REPO, text=True).strip()
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, upstream], cwd=REPO, check=True)
    if not (SHARE / "codes").is_dir() or not (SHARE / "runtimes").is_dir():
        raise FileNotFoundError("Required workstation code/runtime roots unavailable")
    name = "quant-research-workbench-squeeze-v2-" + commit[:9]
    target = SHARE / "codes" / name
    evidence = SHARE / "runtimes" / "vectorized_backtest" / "torch_backtest_v2" / "deployments" / name
    # A fresh directory is required; no mutation of another operator's checkout.
    if target.exists():
        raise ValueError("Deployment directory already exists; use its verified launcher or a new commit")
    stage = require_runtime(DEFAULT / "deployments" / uuid4().hex)
    archive = stage / "source.zip"
    tracked = set(subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=REPO, text=True).splitlines())
    markers = [n for n in ("research/__init__.py", "research/vectorized_backtest/__init__.py",
                          "pipelines/__init__.py", "pipelines/market_sip/__init__.py") if n in tracked]
    subprocess.run(["git", "archive", "--format=zip", "--output", str(archive), commit, *PAYLOAD, *markers], cwd=REPO, check=True)
    hashes = {}
    with ZipFile(archive) as z:
        for item in z.infolist():
            relative = Path(item.filename)
            if item.is_dir():
                continue
            if relative.is_absolute() or ".." in relative.parts or relative.suffix in (".ipynb", ".pyc"):
                raise ValueError("Unexpected committed payload path")
            destination = target / relative
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError("Payload escapes target")
            data = z.read(item)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            digest = sha256(data).hexdigest()
            if sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError("Deployment hash mismatch: " + item.filename)
            hashes[item.filename] = digest
    base = target / "research/vectorized_backtest/v2/torch_backtest"
    h = sha256()
    for path in sorted(base.rglob("*.py")):
        h.update(path.relative_to(base).as_posix().encode())
        h.update(path.read_bytes())
    report = {"commit": commit, "source": "git archive of pushed commit", "files": hashes,
              "verified_files": len(hashes), "v2_code_hash": h.hexdigest(), "target": str(target)}
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "deployment.json").write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"commit": commit, "verified_files": len(hashes), "target": str(target),
                      "workstation_launcher": "D:/TradingML/codes/" + name + "/research/vectorized_backtest/v2/torch_backtest/run_workstation.py"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
