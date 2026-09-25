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
_RUNTIME_ROOT = Path(__file__).parents[1] / "trading_runtime"
_INDIRECT_SOURCES = (_RUNTIME_ROOT / "runtime.py",
                     _RUNTIME_ROOT / "portfolio.py",
                     _RUNTIME_ROOT / "order_management.py",
                     _RUNTIME_ROOT / "risk_supervisor.py")
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


def _fixed_v7_warning_unreachable(source: str) -> bool:
    """Recognize the fixed-mode early return before the legacy V7 warning.

    This deliberately accepts one precise controller structure. A changed
    branch must be reviewed instead of quietly excluding a newly reachable
    warning from the fixed-mode family inventory.
    """
    tree = ast.parse(source)
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "_prepare_v7_coverage"]
    if len(functions) != 1:
        return False
    body = functions[0].body
    guard = body[0] if body else None
    if (not isinstance(guard, ast.If)
            or ast.unparse(guard.test) != "self.definition.mode == RunMode.BACKTEST"):
        return False
    fixed_guards = [node for node in guard.body if isinstance(node, ast.If)
                    and ast.unparse(node.test)
                    == "ExecutionInterval.parse(self.definition.execution_interval).kind == 'fixed'"]
    if len(fixed_guards) != 1 or not isinstance(fixed_guards[0].body[-1], ast.Return):
        return False
    warnings = [node for node in ast.walk(functions[0])
                if isinstance(node, ast.Call) and _literal_pair(node)
                == ("warning", "level_book_coverage")]
    return len(warnings) == 1 and fixed_guards[0].end_lineno < warnings[0].lineno


def certify_direct_v3_projection(*, source_path: Path = _CONTROLLER) -> str:
    """Fail closed on a changed or unsupported direct-emitter family set."""
    source = source_path.read_text(encoding="utf-8")
    families = direct_controller_families(source)
    fixed_families = set(families)
    if ("warning", "level_book_coverage") in fixed_families:
        if not _fixed_v7_warning_unreachable(source):
            raise ValueError("Fixed-mode V7 warning reachability is unproven")
        fixed_families.remove(("warning", "level_book_coverage"))
    unsupported = sorted(fixed_families - _V3_PROJECTED)
    if unsupported:
        raise ValueError(f"V3 direct emitters lack typed projection: {unsupported}")
    payload = {"version": 1, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "direct_families": tuple(sorted(fixed_families))}
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


def indirect_journal_inventory(
    sources: tuple[Path, ...] = _INDIRECT_SOURCES,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Static inventory of reachable collaborators; unresolved emitters remain visible.

    This is deliberately conservative: all paths in the injected Portfolio,
    OMS, runtime and risk supervisor are included, even conditional ones.
    """
    families: set[tuple[str, str]] = set()
    dynamic: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            owner = node.func.value
            is_journal = (isinstance(owner, ast.Attribute)
                          and isinstance(owner.value, ast.Name)
                          and owner.value.id == "self"
                          and owner.attr == "journal")
            is_record = (isinstance(owner, ast.Name) and owner.id == "self"
                         and method == "_record")
            if is_journal and method in {"append", "append_many", "append_once",
                                         "append_once_many"}:
                pair = _literal_pair(node)
                if pair is None and node.args:
                    arg = node.args[0]
                    if isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
                        pair = _literal_pair(arg.elt)
                    elif isinstance(arg, (ast.List, ast.Tuple)):
                        pairs = [_literal_pair(item) for item in arg.elts]
                        if pairs and all(item is not None for item in pairs):
                            families.update(pairs)
                            continue
                if pair is None:
                    dynamic.append(f"{path.name}:{node.lineno}:journal.{method}")
                else:
                    families.add(pair)
            elif is_record and path.name == "portfolio.py":
                kind = node.args[0] if node.args else None
                if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                    families.add(("portfolio_management", kind.value))
                else:
                    dynamic.append(f"{path.name}:{node.lineno}:_record")
            elif is_record and path.name == "order_management.py":
                pair = None
                if len(node.args) >= 2 and all(isinstance(arg, ast.Constant)
                                                and isinstance(arg.value, str)
                                                for arg in node.args[:2]):
                    pair = (node.args[0].value, node.args[1].value)
                if pair is None:
                    dynamic.append(f"{path.name}:{node.lineno}:_record")
                else:
                    families.add(pair)
    return tuple(sorted(families)), tuple(sorted(dynamic))


def certify_indirect_v3_projection(
    sources: tuple[Path, ...] = _INDIRECT_SOURCES,
) -> str:
    """Reject dynamic or unprojected indirect emitters before V3 bootstrap."""
    families, dynamic = indirect_journal_inventory(sources)
    if dynamic:
        raise ValueError(f"V3 indirect journal emitter identity is dynamic: {dynamic}")
    unsupported = sorted(set(families) - _V3_PROJECTED - {
        ("lifecycle", "run"), ("broker", "connection_state"),
        ("risk", "risk_snapshot"), ("risk", "continuous_risk_state"),
        ("strategy", "strategy_intent"),
        ("strategy_decision", "intent_rejection"),
        ("strategy_decision", "intent_deferral"),
        ("execution", "fill"), ("execution", "commission"),
    })
    if unsupported:
        raise ValueError(f"V3 indirect emitters lack typed projection: {unsupported}")
    evidence = [(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in sources]
    return hashlib.sha256(json.dumps({"families": families, "sources": evidence},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()
