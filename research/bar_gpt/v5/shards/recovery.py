"""Explicit engineering-only resume receipt; never rewrite frozen data seals."""

import ast
import json
import importlib.util
from pathlib import Path

from .contract import digest, file_hash


def identity_query(receipt_path):
    """Retain the original logical query seal after an audited execution rewrite.

    New receipts separately record execution_query_hash. Loading the frozen
    baseline only constructs SQL text; it does not execute or read market data.
    """
    from .sql import day_query
    if receipt_path is None:
        return day_query
    receipt = json.loads(Path(receipt_path).read_text())
    if not receipt.get("query_equivalence"):
        return day_query
    path = Path(receipt["baseline_root"]) / "research/bar_gpt/v5/shards/sql.py"
    spec = importlib.util.spec_from_file_location(__package__ + "._identity_sql", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.day_query


def _query_proof(old, current, receipt):
    """Require sealed real-output equivalence and unchanged canonical source selection."""
    proof_path = Path(receipt["query_equivalence"])
    if file_hash(proof_path) != receipt.get("query_equivalence_sha256"):
        raise ValueError("Query-equivalence proof changed")
    proof = json.loads(proof_path.read_text())
    if (not proof.get("passed") or len(proof.get("units", [])) < 32
            or {int(r["day"][:4]) for r in proof.get("units", [])} != set(old["years"])
            or proof["baseline_sql_hash"] != old["code"]["sql.py"]
            or proof["candidate_sql_hash"] != current["code"]["sql.py"]
            or any(r["old_hash"] != r["new_hash"] for r in proof["units"])):
        raise ValueError("Insufficient or mismatched query-equivalence proof")
    baseline = Path(receipt["baseline_root"]) / "research/bar_gpt/v5/shards/sql.py"
    if file_hash(baseline) != proof["baseline_sql_hash"]:
        raise ValueError("Original query implementation changed")
    def source_ast(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return ast.dump(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "source"), include_attributes=False)
    if source_ast(baseline) != source_ast(Path(__file__).with_name("sql.py")):
        raise ValueError("Execution rewrite changes canonical source selection")


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

    Source authority, packing/storage, numerical contract, sampling parameters
    and dictionary must remain identical. An SQL execution rewrite additionally
    requires sealed real-output equivalence across every requested year. The
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
    if "sql.py" in changed:
        _query_proof(old, current, receipt)
        allowed.add("sql.py")
    if not changed <= allowed:
        raise ValueError("Recovery modifies data extraction/storage code.")
    baseline = Path(receipt["baseline_root"]) / "research/bar_gpt/v5/shards/contract.py"
    if file_hash(baseline) != old["code"]["contract.py"]:
        raise ValueError("Original contract implementation is not the pinned baseline.")
    if _data_contract_ast(baseline) != _data_contract_ast(Path(__file__).with_name("contract.py")):
        raise ValueError("Recovery modifies the sample format or data-contract functions.")
    return saved["binding"]
