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

_CONFIG_PREFIXES = {
    "certify_numbered_configuration": ast.parse('''
if type(strategy_number) is int and strategy_number == 43:
    from .backtest_strategy_forty_three_configuration import certify_configuration
    return certify_configuration(client)
''').body[0],
    "is_numbered_fixed_configuration": ast.parse('''
if type(number) is int and number == 43:
    from src.trading_runtime.strategy_forty_three_release import verify_manifest
    verify_manifest(strategy)
    return True
''').body[0],
}

_PREFLIGHT_EXTENSION = ast.parse('''
if (configuration.get("strategy", {}).get("strategy_id"),
        configuration.get("strategy", {}).get("revision")) == ("squeeze-grid-strategy", 43):
    from src.backend.backtest_strategy_forty_three_preflight import preflight
    return preflight(anchor_date=anchor_date, session_count=session_count,
        initial_cash=initial_cash, start_time=start_time, end_time=end_time,
        tickers=tickers, configuration_revision=approved,
        saved_review_authority=_saved_review_authority)
''').body[0]


def _same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def _verify_lineage():
    path = Path(__file__).resolve().parents[1] / "trading_runtime/strategy_forty_three_lineage.py"
    if sha256(ast.unparse(ast.parse(path.read_text(encoding="utf-8"))).encode()).hexdigest() != LINEAGE_AST_HASH:
        raise ValueError("Strategy 43 lineage adapter differs from its reviewed source")


def historical_strategy_tree(tree: ast.Module, relative: str) -> ast.Module:
    relative = relative.replace("\\", "/").removeprefix("src/")
    if relative not in {"backend/backtest_v4_saved_review.py", "pipelines/strategy_one/configuration_publisher.py",
                        "backend/replay_run_service.py", "backend/backtest_strategy_one_configuration.py",
                        "trading_runtime/numbered_fixed_strategy.py",
                        "trading_runtime/arte_journal_commit_v4.py",
                        "trading_runtime/arte_oms_projection.py"}:
        return tree
    result = deepcopy(tree)
    if relative == "backend/backtest_v4_saved_review.py":
        expected = ast.parse('''
if (context["strategy_id"], int(context["strategy_revision"])) == ("squeeze-grid-strategy", 43):
    from .backtest_strategy_forty_three_review import audit_terminal_source
    audit_terminal_source(client, prefix, context)
''').body[0]
        methods = [node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == "_terminal_attestation"]
        matches = [(owner, i) for owner in ast.walk(methods[0]) if hasattr(owner, "body")
                   and isinstance(owner.body, list) for i, node in enumerate(owner.body) if _same(node, expected)]
        if len(methods) != 1 or len(matches) != 1:
            raise ValueError("Strategy 43 cold source dispatch changed")
        owner, index = matches[0]
        owner.body.pop(index)
        return result
    if relative == "pipelines/strategy_one/configuration_publisher.py":
        expected = ast.parse('''
if type(number) is int and number == 43:
    from src.backend.backtest_strategy_forty_three_configuration import verify_envelope
    payload, nodes = verify_envelope(dict(envelope))
''').body[0]
        branches = [node for node in ast.walk(result) if isinstance(node, ast.If) and _same(
            ast.If(test=node.test, body=node.body, orelse=[]), expected)]
        if len(branches) != 1 or len(branches[0].orelse) != 1 or not isinstance(branches[0].orelse[0], ast.If):
            raise ValueError("Strategy 43 configuration producer dispatch changed")
        prior = branches[0].orelse[0]
        branches[0].test, branches[0].body, branches[0].orelse = prior.test, prior.body, prior.orelse
        dictionaries = [node for node in ast.walk(result) if isinstance(node, ast.Dict)]
        changed = 0
        for node in dictionaries:
            for index, key in enumerate(node.keys):
                if isinstance(key, ast.Constant) and key.value == "strategy_id" and ast.unparse(node.values[index]) == "payload['strategy']['strategy_id']":
                    node.values[index] = ast.Name(id="STRATEGY_ID", ctx=ast.Load())
                    changed += 1
        if changed != 1:
            raise ValueError("Strategy 43 published identity binding changed")
        return result
    if relative == "backend/replay_run_service.py":
        nodes = [node for node in result.body if isinstance(node, ast.FunctionDef)
                 and node.name == "backtest_preflight"]
        if len(nodes) != 1:
            raise ValueError("Historical preflight dispatch is ambiguous")
        positions = [i for i, node in enumerate(nodes[0].body) if _same(node, _PREFLIGHT_EXTENSION)]
        if len(positions) != 1:
            raise ValueError("Strategy 43 preflight dispatch differs from reviewed source")
        nodes[0].body.pop(positions[0])
        # Other reviewed additions only dispatch identity 43; restore the exact
        # previous branch for whole-module certificates of strategies 1-42.
        for node in ast.walk(result):
            if isinstance(node, ast.If) and ast.unparse(node.test) == "strategy.get('strategy_id') == 'squeeze-grid-strategy' and strategy.get('revision') == 43":
                if (len(node.body) != 2 or ast.unparse(node.body[0]) !=
                        "from src.backend.backtest_strategy_forty_three_configuration import validate_definition_sources"
                        or ast.unparse(node.body[1]) != "validate_definition_sources(self)"
                        or len(node.orelse) != 1 or not isinstance(node.orelse[0], ast.If)):
                    raise ValueError("Strategy 43 definition branch changed")
                prior = node.orelse[0]
                node.test, node.body, node.orelse = prior.test, prior.body, prior.orelse
            if isinstance(node, ast.keyword) and node.arg == "batch_size" and isinstance(node.value, ast.IfExp):
                if ast.unparse(node.value) != "512 if strategy_number == 43 else 4096":
                    raise ValueError("Strategy 43 bootstrap bound changed")
                node.value = ast.Constant(4096)
        owners = [node for node in result.body if isinstance(node, ast.ClassDef) and node.name == "ReplayRunService"]
        creates = [node for node in owners[0].body if isinstance(node, ast.AsyncFunctionDef) and node.name == "create"]
        expected = ast.parse('''
strategy = definition.configuration_revision.get("payload", {}).get("strategy", {})
if (strategy.get("strategy_id"), strategy.get("revision")) == ("squeeze-grid-strategy", 43):
    from src.backend.backtest_strategy_forty_three_controller import StrategyFortyThreeController
    controller = StrategyFortyThreeController(definition, runtime_root=self.runtime_root)
else:
    controller = ReplayRunController(definition, runtime_root=self.runtime_root)
''').body
        if len(creates) != 1 or not all(_same(left, right) for left, right in zip(creates[0].body[:2], expected)):
            raise ValueError("Strategy 43 service factory changed")
        creates[0].body[:2] = ast.parse("controller = ReplayRunController(definition, runtime_root=self.runtime_root)").body
        return result
    if relative == "backend/backtest_strategy_one_configuration.py":
        for name, expected in _CONFIG_PREFIXES.items():
            nodes = [node for node in result.body if isinstance(node, ast.FunctionDef) and node.name == name]
            if len(nodes) != 1:
                raise ValueError("Historical configuration dispatch is ambiguous")
            positions = [i for i, node in enumerate(nodes[0].body) if _same(node, expected)]
            if len(positions) != 1:
                raise ValueError("Strategy 43 configuration extension differs from its reviewed source")
            nodes[0].body.pop(positions[0])
        patterns = [node for node in ast.walk(result) if isinstance(node, ast.Constant)
                    and isinstance(node.value, str) and node.value.startswith("strategy-one-(?:")]
        if len(patterns) != 1 or "|42|43):" not in patterns[0].value:
            raise ValueError("Strategy 43 configuration selection extension changed")
        patterns[0].value = patterns[0].value.replace("|42|43):", "|42):")
        return result
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
