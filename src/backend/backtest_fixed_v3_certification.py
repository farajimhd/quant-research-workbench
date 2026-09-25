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
    ("portfolio_management", "portfolio_decision"),
    ("portfolio_management", "portfolio_reservation"),
    ("portfolio_management", "portfolio_reconciliation"),
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


def _literal_variants(node: ast.AST | None) -> frozenset[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    if isinstance(node, ast.IfExp):
        left = _literal_variants(node.body)
        right = _literal_variants(node.orelse)
        return left | right if left is not None and right is not None else None
    return None


def _literal_pair_variants(node: ast.AST) -> frozenset[tuple[str, str]] | None:
    if isinstance(node, ast.Call):
        values = {keyword.arg: keyword.value for keyword in node.keywords}
    elif isinstance(node, ast.Dict):
        values = {key.value: value for key, value in zip(node.keys, node.values)
                  if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    else:
        return None
    categories = _literal_variants(values.get("category"))
    entities = _literal_variants(values.get("entity_type"))
    if categories is None or entities is None:
        return None
    return frozenset((category, entity) for category in categories for entity in entities)


def _record_forwarder_safe(node: ast.Call, source_name: str) -> bool:
    values = {keyword.arg: keyword.value for keyword in node.keywords}
    entity = values.get("entity_type")
    if not isinstance(entity, ast.Name) or entity.id != "entity_type":
        return False
    category = values.get("category")
    if source_name == "portfolio.py":
        return isinstance(category, ast.Constant) and category.value == "portfolio_management"
    return (source_name == "order_management.py"
            and isinstance(category, ast.Name) and category.id == "category")


def _entry_expression_pairs(node: ast.AST) -> frozenset[tuple[str, str]] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        groups = [_entry_expression_pairs(item) for item in node.elts]
        return (frozenset(pair for group in groups for pair in group)
                if all(group is not None for group in groups) else None)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
        return _entry_expression_pairs(node.elt)
    return _literal_pair_variants(node)


def _local_entries_pairs(function: ast.AST) -> frozenset[tuple[str, str]] | None:
    """Resolve a local append_many(entries) only when all writes are explicit."""
    groups: list[frozenset[tuple[str, str]]] = []
    initialized = False
    allowed_names: set[int] = set()
    for node in ast.walk(function):
        value: ast.AST | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "entries":
            initialized = True
            allowed_names.add(id(node.target))
            value = node.value
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "entries"
                for target in node.targets):
            initialized = True
            allowed_names.update(id(target) for target in node.targets
                                 if isinstance(target, ast.Name) and target.id == "entries")
            value = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "entries":
            return None
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "entries":
            if node.func.attr not in {"append", "extend"} or len(node.args) != 1:
                return None
            allowed_names.add(id(node.func.value))
            value = node.args[0]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "append_many" and len(node.args) == 1 \
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "entries":
            allowed_names.add(id(node.args[0]))
        if value is not None:
            group = _entry_expression_pairs(value)
            if group is None:
                return None
            groups.append(group)
    if any(isinstance(node, ast.Name) and node.id == "entries"
           and id(node) not in allowed_names for node in ast.walk(function)):
        return None
    return (frozenset(pair for group in groups for pair in group)
            if initialized and groups else None)


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
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        wrappers = [node for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_record"]
        forwarded: list[str] = []
        record_calls = 0
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
                if (path.name in {"portfolio.py", "order_management.py"}
                        and method == "append"
                        and any(wrapper.lineno <= node.lineno <= wrapper.end_lineno
                                for wrapper in wrappers)):
                    forwarded.append(f"{path.name}:{node.lineno}:journal.{method}")
                    if not _record_forwarder_safe(node, path.name):
                        dynamic.append(forwarded[-1])
                    continue
                pairs = _literal_pair_variants(node)
                if pairs is None and node.args:
                    arg = node.args[0]
                    if isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
                        pairs = _literal_pair_variants(arg.elt)
                    elif isinstance(arg, (ast.List, ast.Tuple)):
                        item_pairs = [_literal_pair_variants(item) for item in arg.elts]
                        if item_pairs and all(item is not None for item in item_pairs):
                            families.update(pair for group in item_pairs for pair in group)
                            continue
                    elif method == "append_many" and isinstance(arg, ast.Name) \
                            and arg.id == "entries":
                        scopes = [function for function in functions
                                  if function.lineno <= node.lineno <= function.end_lineno]
                        if scopes:
                            function = min(scopes, key=lambda item: item.end_lineno - item.lineno)
                            pairs = _local_entries_pairs(function)
                if pairs is None:
                    dynamic.append(f"{path.name}:{node.lineno}:journal.{method}")
                else:
                    families.update(pairs)
            elif is_record and path.name == "portfolio.py":
                record_calls += 1
                kind = node.args[0] if node.args else None
                if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                    families.add(("portfolio_management", kind.value))
                else:
                    dynamic.append(f"{path.name}:{node.lineno}:_record")
            elif is_record and path.name == "order_management.py":
                record_calls += 1
                pair = None
                if len(node.args) >= 2 and all(isinstance(arg, ast.Constant)
                                                and isinstance(arg.value, str)
                                                for arg in node.args[:2]):
                    pair = (node.args[0].value, node.args[1].value)
                if pair is None:
                    dynamic.append(f"{path.name}:{node.lineno}:_record")
                else:
                    families.add(pair)
        if forwarded and (len(wrappers) != 1 or len(forwarded) != 1 or record_calls == 0
                          or any(row.startswith(f"{path.name}:") and row.endswith(":_record")
                                 for row in dynamic)):
            dynamic.extend(forwarded)
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
