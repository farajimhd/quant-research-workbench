"""Independent frozen-parent source proof; no financial, execution or resume action."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


_PARENT_COMMIT = "d66a2f151d9128145ed9ca2ea8dd07914b6120c2"
_PARENT_FINGERPRINT = "d4794eceba721508c2fc1e18a11fc68fb65d0f0c45a9b25c4e653b49cfb04484"
_PARENT_PROOF = "ddff9f281fb4afe69c420106046591d14e72559e636ff5e8d3f619295db33821"
_PARENT_ROOTS = {
    "DESKTOP-SAAI85T": r"D:\TradingML\codes\quant-research-workbench-strategy75-native-d66a2f151",
}
_LAPTOP_ROOT = Path.home() / ".codex/worktrees/checkpoint-prefix-successor/quant-research-workbench"

# Isolated Python ignores ambient PYTHONPATH and imports only the fixed parent
# root. This program has no client, credentials, database or runtime entrypoint.
_PROGRAM = r'''
import json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
def identity():
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], text=True)
    from src.backend.historical_runtime_versions import backend_source_fingerprint
    return head, not bool(dirty.strip()), backend_source_fingerprint()
before = identity()
if before != (sys.argv[2], True, sys.argv[3]):
    raise ValueError("Frozen parent source identity differs before proof")
from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
proof = certify_numbered_fixed_v4_projection(75)
after = identity()
if after != before or proof != sys.argv[4]:
    raise ValueError("Frozen parent source identity or proof differs after proof")
print(json.dumps({"schema":1,"strategy_number":75,"commit":before[0],"clean":before[1],"fingerprint":before[2],"native_proof":proof}, separators=(",", ":")))
'''


@dataclass(frozen=True)
class FrozenParentSourceProof:
    strategy_number: int
    commit: str
    fingerprint: str
    native_proof: str


def _unique_object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("Frozen parent proof response contains duplicate fields")
        value[name] = item
    return value


def certify_frozen_performance_parent_source() -> FrozenParentSourceProof:
    """Reverify the fixed catalog parent in a fresh namespace, once per operation.

    No caller-supplied root, proof token, source override or cached approval is
    accepted. Current successor source must independently pass its own proof.
    """
    root = Path(_PARENT_ROOTS.get(platform.node().upper(), _LAPTOP_ROOT)).resolve()
    if not root.is_dir():
        raise RuntimeError("Frozen parent catalog checkout is unavailable")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PROGRAM, str(root),
         _PARENT_COMMIT, _PARENT_FINGERPRINT, _PARENT_PROOF],
        cwd=root, env=env, capture_output=True, timeout=300, check=False,
    )
    if result.returncode or len(result.stdout) > 4096:
        raise RuntimeError("Frozen parent source-only proof failed")
    try:
        value = json.loads(result.stdout, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError("Frozen parent proof response is invalid") from exc
    expected = {"schema": 1, "strategy_number": 75, "commit": _PARENT_COMMIT,
                "clean": True, "fingerprint": _PARENT_FINGERPRINT,
                "native_proof": _PARENT_PROOF}
    if (type(value) is not dict or json.dumps(value, sort_keys=True, separators=(",", ":"))
            != json.dumps(expected, sort_keys=True, separators=(",", ":"))):
        raise ValueError("Frozen parent proof response differs from catalog authority")
    return FrozenParentSourceProof(75, _PARENT_COMMIT, _PARENT_FINGERPRINT, _PARENT_PROOF)
