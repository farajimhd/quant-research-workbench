"""Exact source projection for the reviewed Strategy 45 launch routing repair.

No financial configuration or source table is rewritten. Only the corrected
definition branch and its explicit preflight qualification are projected back
to the already-published source fingerprint. Every other backend byte remains
bound to that fingerprint. The caller pins this proof's exact source digest.
"""
from hashlib import sha256
from pathlib import Path

IMPORT = "from pipelines.strategy_one.strategy_forty_five_routing_repair import qualified_source_fingerprint\n"
OLD_CHECK = 'if current != LOADED_BACKEND_FINGERPRINT or current != manifest["approved_code_fingerprint"]:'
NEW_CHECK = 'if current != LOADED_BACKEND_FINGERPRINT or qualified_source_fingerprint(proof_digest="{digest}") != manifest["approved_code_fingerprint"]:'
OLD_BRANCH = '            if strategy.get("strategy_id") == "squeeze-grid-strategy" and strategy.get("revision") == 44:'
NEW_BRANCH = OLD_BRANCH.replace("            if ", "            elif ")


def qualified_source_fingerprint(*, proof_digest, root=None):
    if sha256(Path(__file__).read_bytes().replace(b"\r\n",b"\n")).hexdigest() != proof_digest:
        raise RuntimeError("Strategy 45 routing repair proof source changed")
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    files = [*(root / "src/backend").rglob("*.py"), *(root / "src/trading_runtime").rglob("*.py")]
    digest = sha256()
    seen = set()
    for path in sorted(files,key=lambda p:p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8").replace("\r\n","\n")
        if relative == "src/backend/replay_run_service.py":
            if source.count(NEW_BRANCH) != 1:
                raise RuntimeError("Strategy 45 routing repair branch differs")
            source = source.replace(NEW_BRANCH,OLD_BRANCH,1)
            seen.add(relative)
        if relative == "src/backend/backtest_strategy_forty_five_preflight.py":
            check = NEW_CHECK.format(digest=proof_digest)
            if source.count(IMPORT) != 1 or source.count(check) != 1:
                raise RuntimeError("Strategy 45 routing repair qualification differs")
            source = source.replace(IMPORT,"",1).replace(check,OLD_CHECK,1)
            seen.add(relative)
        digest.update(relative.encode()); digest.update(b"\n")
        digest.update(source.encode()); digest.update(b"\0")
    if len(seen) != 2:
        raise RuntimeError("Strategy 45 routing repair source is incomplete")
    return digest.hexdigest()
