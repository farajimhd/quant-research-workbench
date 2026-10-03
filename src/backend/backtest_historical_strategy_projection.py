"""Exact projection of a new independent dispatch for historical certificates.

Older numbered policies still verify their original reviewed AST hashes. This
adapter may remove ONLY the exact reviewed Strategy 43 identity prefix,
command-lineage extension and cold-order dispatch. It cannot erase historical
statements, change historical constants or accept replacement behavior hashes.
The actual installed source remains included in each source receipt.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

_PREFIX = ast.parse('''
if strategy_id == "squeeze-grid-strategy" and type(revision) is int and revision == 43:
    return True
''').body[0]

_COMMAND_EXTENSION = ast.parse('''
from .strategy_forty_three_lineage import command_ids
strategy_one_commands.update(command_ids(command_rows))
''').body

_COLD_PREFIX = ast.parse('''
if (isinstance(getattr(state, "group", None), dict)
        and state.group.get("strategy_id") == "squeeze-grid-strategy"
        and type(state.group.get("strategy_revision")) is int
        and state.group["strategy_revision"] == 43):
    from .strategy_forty_three_lineage import reconstruct_leg_orders
    if any(row is not None for row in (followthrough_row, profit_giveback_row,
                                      confirmed_ah_row, liquidity_fade_row)):
        raise ValueError("Strategy 43 cannot inherit another strategy's exit witness")
    return reconstruct_leg_orders(state, source_intent, protection_history,
        admission_reservation=admission_reservation, admission_decision=admission_decision)
''').body[0]

LINEAGE_AST_HASH = "e0474cc8c5233574fba3a5ef9b3e2fedd9a054fdb72f6ffd6d4c7e69a20d2074"


def _same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def _verify_lineage():
    path = Path(__file__).resolve().parents[1] / "trading_runtime/strategy_forty_three_lineage.py"
    if sha256(ast.unparse(ast.parse(path.read_text(encoding="utf-8"))).encode()).hexdigest() != LINEAGE_AST_HASH:
        raise ValueError("Strategy 43 lineage adapter differs from its reviewed source")


def historical_strategy_tree(tree: ast.Module, relative: str) -> ast.Module:
    relative = relative.replace("\\", "/").removeprefix("src/")
    if relative not in {"trading_runtime/numbered_fixed_strategy.py",
                        "trading_runtime/arte_journal_commit_v4.py",
                        "trading_runtime/arte_oms_projection.py"}:
        return tree
    result = deepcopy(tree)
    if relative == "trading_runtime/arte_journal_commit_v4.py":
        nodes = [node for node in result.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_publish_typed_batch_v4"]
        if len(nodes) != 1:
            raise ValueError("Historical command graph is ambiguous")
        node = nodes[0]
        positions = [i for i, item in enumerate(node.body)
                     if isinstance(item, ast.ImportFrom)
                     and item.module == "strategy_forty_three_lineage"]
        if positions:
            if (len(positions) != 1 or positions[0] == 0
                    or positions[0] + 1 >= len(node.body)
                    or not all(_same(item, expected) for item, expected in
                        zip(node.body[positions[0]:positions[0] + 2], _COMMAND_EXTENSION))
                    or not isinstance(node.body[positions[0] - 1], ast.Assign)
                    or ast.unparse(node.body[positions[0] - 1].targets[0]) != "strategy_one_commands"):
                raise ValueError("Strategy 43 command extension differs from its reviewed source")
            _verify_lineage()
            del node.body[positions[0]:positions[0] + 2]
        return result
    if relative == "trading_runtime/arte_oms_projection.py":
        nodes = [node for node in result.body if isinstance(node, ast.FunctionDef)
                 and node.name == "reconstruct_strategy_one_oms_lineage"]
        if len(nodes) != 1:
            raise ValueError("Historical OMS lineage is ambiguous")
        node = nodes[0]
        if len(node.body) > 1 and isinstance(node.body[1], ast.If):
            if not _same(node.body[1], _COLD_PREFIX):
                raise ValueError("Strategy 43 cold dispatch differs from its reviewed source")
            _verify_lineage()
            node.body.pop(1)
        return result
    nodes = [node for node in result.body if isinstance(node, ast.FunctionDef)
             and node.name == "is_numbered_fixed_strategy"]
    if len(nodes) != 1:
        raise ValueError("Historical numbered identity is ambiguous")
    node = nodes[0]
    if len(node.body) == 1:
        return result
    if len(node.body) != 2 or not _same(node.body[0], _PREFIX):
        raise ValueError("Strategy 43 independent dispatch differs from its reviewed prefix")
    node.body.pop(0)
    return result
