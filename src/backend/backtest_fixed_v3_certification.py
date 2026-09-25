"""Inactive, source-bound checks for fixed V3 journal and squeeze authority.

This certifies direct controller emitters only. Indirect portfolio/OMS emitters
need a separate exhaustive inventory before this can authorize a launch.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.backend.fixed_bar_signal import first_squeeze_sql, validate_stream


_CONTROLLER = Path(__file__).with_name("replay_run_service.py")
_V3_PROJECTED = frozenset({
    ("checkpoint", "market_boundary"),
    ("data_authority", "source_revision"),
    ("strategy_decision", "signal"),
    ("resource_lease", "prepared_v7_stream"),
    ("market_discovery_signal", "signal_occurrence"),
})


def _literal_pair(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Call):
        values = {keyword.arg: keyword.value for keyword in node.keywords}
    elif isinstance(node, ast.Dict):
        values = {key.value: value for key, value in zip(node.keys, node.values)
                  if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    else:
        return None
    category = values.get("category")
    entity_type = values.get("entity_type")
    if not isinstance(category, ast.Constant) or not isinstance(category.value, str):
        return None
    if not isinstance(entity_type, ast.Constant) or not isinstance(entity_type.value, str):
        return None
    return category.value, entity_type.value


def direct_controller_families(source: str) -> tuple[tuple[str, str], ...]:
    """Inventory literal families at direct ``self._journal`` call sites.

    Dynamic emitter shapes are rejected, not silently excluded. This is not an
    inventory of downstream collaborators that receive the journal instance.
    """
    tree = ast.parse(source)
    families: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute)
                and target.attr in {"append", "append_once", "append_many", "append_once_many"}
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "_journal"
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "self"):
            continue
        pair = _literal_pair(node)
        if pair is None and node.args:
            argument = node.args[0]
            if isinstance(argument, (ast.GeneratorExp, ast.ListComp)):
                pair = _literal_pair(argument.elt)
            elif isinstance(argument, (ast.List, ast.Tuple)) and len(argument.elts) == 1:
                pair = _literal_pair(argument.elts[0])
        if pair is None:
            raise ValueError(f"Dynamic direct journal emitter at line {node.lineno}")
        families.add(pair)
    if not families:
        raise ValueError("No direct controller journal emitters found")
    return tuple(sorted(families))


def certify_direct_v3_projection(*, source_path: Path = _CONTROLLER) -> str:
    """Fail closed on a changed or unsupported direct-emitter family set."""
    source = source_path.read_text(encoding="utf-8")
    families = direct_controller_families(source)
    unsupported = sorted(set(families) - _V3_PROJECTED)
    if unsupported:
        raise ValueError(f"V3 direct emitters lack typed projection: {unsupported}")
    payload = {"version": 1, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "direct_families": families}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def certify_pinned_squeeze_query(
    plan: Any, *, stream: Mapping[str, Any], activation: Mapping[str, Any],
    through_boundary_ms: int, expected_query_sha256: str,
) -> str:
    """Return the exact query hash only for the pinned scanner and boundary."""
    validate_stream(stream, activation)
    query = first_squeeze_sql(plan, through_boundary_ms=through_boundary_ms)
    digest = hashlib.sha256(query.encode()).hexdigest()
    if digest != expected_query_sha256:
        raise ValueError("Pinned squeeze query hash changed")
    return digest
