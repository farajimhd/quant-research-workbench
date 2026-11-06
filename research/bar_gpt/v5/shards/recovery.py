"""Explicit engineering-only resume receipt; never rewrite frozen data seals."""

import ast
import json
from pathlib import Path

from .contract import digest, file_hash


def _data_contract_ast(path):
    """Exclude only JSON publication and its time import from contract.py."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.FunctionDef) and node.name == "write_json")
                 and not (isinstance(node, ast.Import) and len(node.names) == 1
                          and node.names[0].name == "time")]
    return ast.dump(tree, include_attributes=False)


def resolve_binding(saved, current, receipt_path):
    """Accept only an explicitly recorded, exact old→new code migration.

    Extraction SQL, source authority, packing/storage, numerical contract,
    sampling parameters, and source dictionary must remain identical. The
    separately reviewed controller/observer/test edits are pinned by exact
    code hashes in the recovery receipt. Existing shard identities are retained.
    """
    old = saved["contract"]
    if saved["binding"] != digest(old):
        raise ValueError("Frozen contract binding is corrupt.")
    if saved["binding"] == digest(current):
        return saved["binding"]
    if receipt_path is None:
        raise ValueError("Dataset code differs; an audited resume-code receipt is required.")
    receipt = json.loads(Path(receipt_path).read_text())
    if (receipt.get("old_binding") != saved["binding"]
            or receipt.get("new_contract_hash") != digest(current)
            or receipt.get("new_code") != current["code"]):
        raise ValueError("Resume-code receipt does not match these exact code/data contracts.")
    if {k:v for k,v in old.items() if k != "code"} != {k:v for k,v in current.items() if k != "code"}:
        # JSON converts tuples to lists; compare canonical digests below instead.
        if digest({k:v for k,v in old.items() if k != "code"}) != digest({k:v for k,v in current.items() if k != "code"}):
            raise ValueError("Recovery must not change sampling, representation, or authority.")
    changed = {name for name in set(old["code"]) | set(current["code"])
               if old["code"].get(name) != current["code"].get(name)}
    allowed = {"contract.py", "build.py", "progress.py", "test_shards.py", "recovery.py"}
    if not changed <= allowed:
        raise ValueError("Recovery modifies data extraction/storage code.")
    baseline = Path(receipt["baseline_root"]) / "research/bar_gpt/v5/shards/contract.py"
    if file_hash(baseline) != old["code"]["contract.py"]:
        raise ValueError("Original contract implementation is not the pinned baseline.")
    if _data_contract_ast(baseline) != _data_contract_ast(Path(__file__).with_name("contract.py")):
        raise ValueError("Recovery modifies the sample format or data-contract functions.")
    return saved["binding"]
