"""Source-bound, fail-closed journal-family certificate for Strategy 1 V4.

This is deliberately stricter than a sample-run inventory: a quiet market day
cannot prove that an unexercised OMS or broker branch is normalized. Until each
reachable family is projected or its fixed-path exclusion is proved, launch
preflight must reject the runtime source tree.
"""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

from src.backend.backtest_fixed_v3_certification import (
    _INDIRECT_SOURCES, _V3_PROJECTED,
    _FIXED_UNREACHABLE_ADAPTIVE_REPRICE,
    certify_direct_v3_projection, certify_fixed_adaptive_reprice_unreachable,
    indirect_journal_inventory,
)


_COMMON_TYPED = frozenset({
    ("lifecycle", "run"),
    ("broker", "connection_state"),
    ("risk", "risk_snapshot"),
    ("risk", "continuous_risk_state"),
    ("strategy", "strategy_intent"),
    ("strategy_decision", "intent_rejection"),
    ("strategy_decision", "intent_deferral"),
    ("execution", "fill"),
    ("execution", "commission"),
})

# These V4 families have a dedicated typed projector and cold-readback test.
# Do not add a family merely because a table with a similar name exists.
_V4_ADDITIONS = frozenset({
    ("broker", "order_acknowledgement"),
    ("command", "order_cancel"),
    ("broker", "order_cancel_requested"),
    ("broker", "order_repriced"),
    ("broker", "order_reprice_error"),
    ("risk", "kill_entry_order"),
    ("risk", "emergency_flatten"),
    ("order_management", "order_group_state"),
    ("order_management", "protection_reconciliation"),
    ("snapshot", "portfolio"),
    ("snapshot", "position"),
    ("order_management", "protection_replacement_deferred"),
})
_SIMULATED_BROKER = Path(__file__).parents[1] / "trading_runtime" / "simulated_broker.py"
_STRATEGY_ONE_INTENT = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_intent.py"
_STRATEGY_ONE_CONTRACT = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_contract.py"
_STRATEGY_ONE_RUNTIME = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_runtime.py"
_STRATEGY_ONE_EXECUTION = Path(__file__).with_name("backtest_strategy_one_execution.py")
_NUMBERED_FIXED_CONTRACT = Path(__file__).parents[1] / "trading_runtime" / "numbered_fixed_strategy.py"


def _certify_numbered_identity(path: Path = _NUMBERED_FIXED_CONTRACT) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    predicates = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "is_numbered_fixed_strategy"]
    expected = "return strategy_id == STRATEGY_ID and type(revision) is int and (revision in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32))"
    if (len(predicates) != 1 or len(predicates[0].body) != 1
            or ast.unparse(predicates[0].body[0]) != expected):
        raise ValueError("Numbered fixed identity whitelist changed")
    return sha256(source.encode()).hexdigest()


def certify_numbered_fixed_v4_projection(strategy_number: int) -> str:
    """Extend the full inventory proof with Strategy 2's explicit session lane."""
    if strategy_number == 32:
        raise ValueError('Strategy 32 native profit-route integration is not yet certified')
    if strategy_number == 31:
        from .backtest_strategy_profit_certification import certify_profit_giveback_route_source
        from src.trading_runtime.strategy_thirty_one_release import release_contract
        # Reuse all inherited execution proofs rather than bypassing their
        # numbered rule checks; bind the complete additional route separately.
        parent_proof = certify_numbered_fixed_v4_projection(30)
        profit_proof = certify_profit_giveback_route_source()
        release = release_contract()
        release.verify()
        return sha256(json.dumps((parent_proof, profit_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(strategy_number)
    followthrough_proof = certify_followthrough_failure_v4_source() if strategy_number in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    entry_scope_proof = certify_empty_exclusion_entry_scope_source() if strategy_number in (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    early_failure_proof = certify_early_followthrough_failure_v4_source() if strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    recent_bos_proof = certify_recent_bos_entry_source() if strategy_number in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    rising_momentum_proof = certify_rising_momentum_entry_source() if strategy_number in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    base = certify_strategy_one_v4_projection()
    if strategy_number == 1:
        return base
    paths = (_NUMBERED_FIXED_CONTRACT, _STRATEGY_ONE_EXECUTION,
             Path(__file__).parents[1] / "trading_runtime" / "runtime.py",
             Path(__file__).parents[1] / "trading_runtime" / "order_management.py",
             Path(__file__).parents[1] / "trading_runtime" / "numbered_session_exit.py",
             Path(__file__).with_name("backtest_strategy_one_static_gate.py"),
             Path(__file__).with_name("backtest_strategy_one_management.py"),
             Path(__file__).parents[1] / "trading_runtime" / "strategy_one_position.py",
             Path(__file__).parents[1] / "trading_runtime" / "strategy_one_intent.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    def named(tree, name):
        nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == name]
        if len(nodes) != 1:
            raise ValueError(f"Strategy 2 source lane missing or ambiguous: {name}")
        return nodes[0]
    def calls(node):
        return {item.func.attr if isinstance(item.func, ast.Attribute) else item.func.id
                for item in ast.walk(node) if isinstance(item, ast.Call)
                and isinstance(item.func, (ast.Name, ast.Attribute))}
    clock = named(trees[2], "advance_numbered_session_clock")
    exit_source = named(trees[2], "submit_numbered_session_exit")
    cutoff = named(trees[3], "cancel_numbered_session_acquisitions")
    before = named(trees[1], "observe_numbered_boundary")
    finish = named(trees[1], "finish_numbered_boundary")
    intent = named(trees[4], "numbered_session_exit_intent")
    if (not contract.allows_session_exit
            or "cancel_numbered_session_acquisitions" not in calls(clock)
            or "acquisition_cutoff" not in calls(clock)
            or not {"numbered_session_exit_intent", "_execute_intents", "liquidation_due"} <= calls(exit_source)
            or not {"_cancel_open_entry_roots", "reconcile"} <= calls(cutoff)
            or calls(cutoff) & {"_record", "submit_order", "on_liquidity_bar"}
            or "advance_numbered_session_clock" not in calls(before)
            or "financially_active_tickers" not in calls(finish)
            or not any(isinstance(node, ast.Raise) for node in ast.walk(finish))):
        raise ValueError("Strategy 2 session command ordering proof failed")
    if not (isinstance(finish.body[0], ast.Expr)
            and isinstance(finish.body[0].value, ast.Await)
            and ast.unparse(finish.body[0].value.value) == "finish_boundary(work)"):
        raise ValueError("Numbered terminal cursor must complete before residual failure")
    if strategy_number in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        gate = named(trees[5], "compile_static_entry_gate")
        if not {"fromiter", "flatnonzero"} <= calls(gate):
            raise ValueError("Strategy 3 activation gate must remain vectorized")
        masks = [node for node in ast.walk(gate) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "same_session"
                         for target in node.targets)]
        expected_mask = "(starts <= boundaries) & ((starts > 0) & (boundaries < 19500000) | (starts > 43200000) & (boundaries < 57000000))"
        if len(masks) != 1 or ast.unparse(masks[0].value) != expected_mask:
            raise ValueError("Strategy 3 activation session boundaries changed")
        gates = [node for node in ast.walk(trees[1]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "compile_static_entry_gate"]
        selected_gates = [node for node in gates if any(key.arg == "strategy_number"
                and ast.unparse(key.value) == "runtime.config.strategy_revision"
                for key in node.keywords)]
        preliminary_gates = [node for node in gates if any(key.arg == "strategy_number"
                and ast.unparse(key.value) == "12" for key in node.keywords)]
        momentum_routes = [node for node in ast.walk(trees[1]) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "runtime.config.strategy_revision in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31)"]
        if (len(gates) != 2 or len(selected_gates) != 1 or len(preliminary_gates) != 1
                or len(momentum_routes) != 1
                or preliminary_gates[0] not in tuple(ast.walk(momentum_routes[0]))):
            raise ValueError("Strategy 3 static gate is not bound to its selected contract")
    if strategy_number in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        management = named(trees[6], "on_management")
        guard = [node for node in management.body if isinstance(node, ast.If)
                 and "not self.contract.allows_adds" in ast.unparse(node.test)]
        confirmations = [node for node in management.body if isinstance(node, ast.Assign)
                         and any(ast.unparse(target) == "self._positions[key]" for target in node.targets)]
        add_source = named(trees[2], "submit_strategy_one_add")
        submission_guards = [node for node in add_source.body if isinstance(node, ast.If)
            and ast.unparse(node.test) == "not numbered_fixed_strategy(proposal.strategy_number).allows_adds"
            and len(node.body) == 1 and isinstance(node.body[0], ast.Raise)]
        submissions = [node for node in ast.walk(add_source) if isinstance(node, ast.Call)
                       and isinstance(node.func, (ast.Name, ast.Attribute))
                       and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
                       in {"strategy_one_add_intent", "_execute_intents"}]
        if (len(guard) != 1 or len(guard[0].body) != 1
                or not isinstance(guard[0].body[0], ast.Return)
                or not confirmations or confirmations[-1].lineno >= guard[0].lineno
                or len(submission_guards) != 1 or not submissions
                or any(node.lineno <= submission_guards[0].lineno for node in submissions)):
            raise ValueError("Strategy 4 must prohibit adds after confirmed protection")
    if strategy_number in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        reducer = named(trees[7], "advance_protection")
        swing = [node for node in reducer.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "swing" for target in node.targets)]
        reducer_calls = [node for node in ast.walk(named(trees[6], "on_management"))
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                         and node.func.id == "advance_protection"]
        defaults = dict(zip((arg.arg for arg in reducer.args.kwonlyargs), reducer.args.kw_defaults))
        if (len(swing) != 1 or not isinstance(swing[0].value, ast.IfExp)
                or ast.unparse(swing[0].value.test) != "allows_completed_30s_trailing"
                or ast.unparse(swing[0].value.orelse) != "None"
                or not isinstance(swing[0].value.body, ast.Call)
                or ast.unparse(swing[0].value.body.func) != "_low"
                or ast.unparse(defaults["allows_completed_30s_trailing"]) != "True"
                or len(reducer_calls) != 1 or not any(key.arg == "allows_completed_30s_trailing"
                    and ast.unparse(key.value) == "self.contract.allows_completed_30s_trailing"
                    for key in reducer_calls[0].keywords)):
            raise ValueError("Strategy 5 must disable only the subsequent 30s-low stop branch")
    if strategy_number == 7:
        trailing = named(trees[0], "allows_completed_30s_trailing")
        manager = named(trees[6], "on_management")
        bound = [node for node in ast.walk(manager) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "advance_protection"]
        if (len(trailing.body) != 1
                or ast.unparse(trailing.body[0]) != "return self.strategy_number not in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)"
                or len(bound) != 1 or not any(key.arg == "allows_completed_30s_trailing"
                    and ast.unparse(key.value) == "self.contract.allows_completed_30s_trailing"
                    for key in bound[0].keywords)):
            raise ValueError("Strategy 7 must restore the existing completed-low trailing branch")
    if strategy_number in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        reducer = named(trees[7], "advance_protection")
        targets = [node for node in reducer.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "target_amendment" for target in node.targets)]
        defaults = dict(zip((arg.arg for arg in reducer.args.kwonlyargs), reducer.args.kw_defaults))
        bindings = [node for node in ast.walk(named(trees[6], "on_management"))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "advance_protection"]
        if (len(targets) != 1 or not isinstance(targets[0].value, ast.IfExp)
                or ast.unparse(targets[0].value.test) != "price_bearing_bar and allows_target_escalation"
                or ast.unparse(targets[0].value.orelse) != "None"
                or not isinstance(targets[0].value.body, ast.Call)
                or ast.unparse(targets[0].value.body.func) != "ordinal_target"
                or defaults.get("allows_target_escalation") is None
                or ast.unparse(defaults["allows_target_escalation"]) != "True"
                or len(bindings) != 1 or not any(key.arg == "allows_target_escalation"
                    and ast.unparse(key.value) == "self.contract.allows_target_escalation"
                    for key in bindings[0].keywords)):
            raise ValueError("Strategy 6 must freeze only subsequent target escalation")
    if strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        cap = named(trees[0], "caps_entry_at_reference_ask")
        entry = named(trees[8], "strategy_one_entry_intent")
        envelopes = [node for node in ast.walk(entry) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "ExecutionEnvelope"]
        policies = [node for node in ast.walk(entry) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "ExecutionPolicy"]
        envelope_keys = {key.arg: ast.unparse(key.value) for key in envelopes[0].keywords} if len(envelopes) == 1 else {}
        policy_keys = {key.arg: ast.unparse(key.value) for key in policies[0].keywords} if len(policies) == 1 else {}
        if (len(cap.body) != 1 or ast.unparse(cap.body[0]) != "return self.strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)"
                or envelope_keys.get("maximum_buy_price") != "proposal.reference_ask if numbered_fixed_strategy(proposal.strategy_number).caps_entry_at_reference_ask else None"
                or envelope_keys.get("persist_until_cancelled") != "True"
                or policy_keys.get("partial_fill_policy") != "PartialFillPolicy.COMPLETE_REMAINDER"):
            raise ValueError("Strategy 8 must cap acquisition at original reference ask without changing persistence")
    intents = [node for node in ast.walk(intent) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name) and node.func.id == "StrategyIntent"]
    keywords = {key.arg: ast.unparse(key.value) for key in intents[0].keywords} if len(intents) == 1 else {}
    if (keywords.get("action") != "'exit'" or keywords.get("metadata") != "{}"
            or keywords.get("reason") != "numbered_session_exit_reason(strategy_number)"):
        raise ValueError("Strategy 2 liquidation source is not a normalized scalar exit")
    return sha256(json.dumps({"strategy_number": strategy_number, "inventory": base, "followthrough": followthrough_proof, "entry_scope": entry_scope_proof, "early_failure": early_failure_proof, "recent_bos": recent_bos_proof, "rising_momentum": rising_momentum_proof,
        "identity": _certify_numbered_identity(), "session_exit_reason": certify_numbered_session_exit_reason_source(), "sources": tuple(
            sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()



def certify_early_followthrough_failure_v4_source(*, source_path: Path | None = None,
                                                management_path: Path | None = None) -> str:
    """Strategy 11 binds the inclusive minute bound to first held authority."""
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (source_path or runtime_root / "strategy_early_followthrough_failure.py",
             management_path or Path(__file__).with_name("backtest_strategy_one_management.py"),
             runtime_root / "arte_followthrough_failure_v4.py",
             runtime_root / "runtime.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    functions = [n for n in trees[0].body if isinstance(n, ast.FunctionDef)
                 and n.name == "early_followthrough_failure"]
    constants = [n for n in trees[0].body if isinstance(n, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "EARLY_FAILURE_WINDOW_MS" for target in n.targets)]
    if (len(functions) != 1 or len(constants) != 1
            or ast.unparse(constants[0].value) != "60000"):
        raise ValueError("Strategy 11 early failure bound changed")
    function = functions[0]
    substantive = [n for n in function.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    expected = ("witness = followthrough_failure(value)",
                "if witness is None or witness.boundary_ms - witness.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS:\n    return None",
                "return witness")
    if tuple(ast.unparse(n) for n in substantive) != expected:
        raise ValueError("Strategy 11 must preserve original failure conditions and inclusive first-held age")
    assignments = [n for n in ast.walk(trees[1]) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "failure_rule" for t in n.targets)]
    if (len(assignments) != 1 or ast.unparse(assignments[0].value)
            != "zero_regime_risk_failure if self.contract.strategy_number in (30, 31) else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure"):
        raise ValueError("Strategy 11 early failure predicate is not exclusively routed")
    numbered = [n for n in trees[2].body if isinstance(n, ast.FunctionDef)
                and n.name == "validate_numbered_failure"]
    if (len(numbered) != 1
            or "strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28)" not in ast.unparse(numbered[0])
            or "witness.boundary_ms - witness.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS" not in ast.unparse(numbered[0])):
        raise ValueError("Strategy 11 normalized witness must preserve its inclusive first-held bound")
    execute = [n for n in ast.walk(trees[3]) if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "_execute_intents"]
    if (len(execute) != 1 or not any(isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "validate_numbered_failure"
            for n in ast.walk(execute[0]))):
        raise ValueError("Strategy 11 runtime must validate its numbered failure witness")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()


def certify_empty_exclusion_entry_scope_source(*, source_path: Path | None = None) -> str:
    """Bind Strategy 10 to read-only full certification before scope projection."""
    path = source_path or Path(__file__).with_name("backtest_strategy_one_entry_store.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    scope = functions.get("_certify_empty_candidate_exclusion_scope")
    entry = functions.get("certify_entry_evidence_plan")
    if scope is None or entry is None:
        raise ValueError("Strategy 10 scoped entry source certificate is missing")
    def calls(node):
        return {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, (ast.Name, ast.Attribute))}
    required = {"certify_candidate_plan", "exclude_candidate_tickers",
                "load_strategy_one_activations", "certify_hod_plan",
                "certify_entry_evidence_plan", "array_equal"}
    if (not required <= calls(scope)
            or "_certify_empty_candidate_exclusion_scope" not in calls(entry)
            or calls(scope) & {"execute", "insert", "materialize", "build"}):
        raise ValueError("Strategy 10 scope must certify original products and exact projection read-only")
    text = ast.unparse(scope)
    if ("row.candidate_count != 0" not in text
            or "original_activations.rows != activations.rows" not in text
            or "original_hod.contexts != hod.contexts" not in text
            or "strategy-one-empty-exclusion-entry-scope-v1" not in text
            or "original.token" not in text or "candidates.excluded_tickers" not in text):
        raise ValueError("Strategy 10 scope source lost its empty exclusion or original token guards")
    return sha256(source.encode()).hexdigest()


def certify_followthrough_failure_v4_source() -> str:
    """Bind the new rule, its routing and dedicated normalized durable source.

    This supplements the full emitter inventory; it never admits another
    generic event family merely because a similarly named table exists.
    """
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (runtime_root / "strategy_followthrough_failure.py",
             runtime_root / "strategy_followthrough_exit.py",
             runtime_root / "arte_followthrough_failure_v4.py",
             runtime_root / "runtime.py",
             Path(__file__).with_name("backtest_strategy_one_management.py"),
             runtime_root / "arte_journal_writer.py",
             runtime_root / "arte_journal_commit_v4.py",
             Path(__file__).with_name("backtest_typed_projection.py"),
             Path(__file__).with_name("backtest_typed_publisher.py"),
             Path(__file__).with_name("backtest_journal_memory.py"))
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    def named(tree, name):
        found = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(found) != 1:
            raise ValueError(f"Strategy 9 normalized followthrough source proof missing: {name}")
        return found[0]
    def calls(node):
        return {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
                for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, (ast.Name, ast.Attribute))}
    rule = named(trees[0], "followthrough_failure")
    rule_text = ast.unparse(rule)
    expected = ("value.boundary_ms % 5000", "value.completed_five_second_boundary_ms != value.boundary_ms",
                "value.boundary_ms - 5000 < value.first_held_boundary_ms",
                "0 <= value.quote_age_us <= 1000000", "value.macd_line >= value.macd_signal",
                "value.bid > threshold", "value.completed_five_second_close_int > threshold * 10000")
    if any(value not in rule_text for value in expected):
        raise ValueError("Strategy 9 completed failure predicate source changed")
    factory = named(trees[1], "followthrough_exit_intent")
    if "validate_witness" not in calls(factory):
        raise ValueError("Strategy 9 exit factory must revalidate its scalar witness")
    intents = [n for n in ast.walk(factory) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "StrategyIntent"]
    keys = {k.arg: ast.unparse(k.value) for k in intents[0].keywords} if len(intents) == 1 else {}
    if keys.get("metadata") != "{}" or keys.get("action") != "'exit'" or keys.get("reason") != "'strategy_nine_followthrough_failure'":
        raise ValueError("Strategy 9 exit must use a metadata-free normalized intent")
    management = named(trees[4], "on_management")
    selectors = [n for n in ast.walk(management) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "failure_rule" for t in n.targets)]
    if (len(selectors) != 1 or ast.unparse(selectors[0].value)
            != "zero_regime_risk_failure if self.contract.strategy_number in (30, 31) else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure"):
        raise ValueError("Original failure rule must remain routed exclusively to Strategy 9/10")
    submit = named(trees[3], "submit_followthrough_failure")
    execute = named(trees[3], "_execute_intents")
    if (not {"failure_rule", "submit_followthrough_failure"} <= calls(management)
            or not {"followthrough_exit_intent", "_execute_intents"} <= calls(submit)
            or "append_followthrough_exit" not in calls(execute)):
        raise ValueError("Strategy 9 normalized followthrough source proof routing is not wired")
    projection = named(trees[7], "project_pending_backtest_v4_prefix")
    publisher = named(trees[8], "_drain")
    source = named(trees[9], "append_followthrough_exit")
    if (not {"followthrough_exit_for_record", "project_followthrough_failure", "V4FollowThroughFailureBatch"} <= calls(projection)
            or "submit_followthrough_exit_v4" not in calls(publisher)
            or "validate_numbered_failure" not in calls(source)):
        raise ValueError("Strategy 9 normalized followthrough source proof publisher is not wired")
    project = named(trees[2], "project_followthrough_failure")
    seal = named(trees[2], "seal_followthrough_rows")
    if "validate_numbered_failure" not in calls(project) or not {"restore_failure", "_source_entry", "typed_row"} <= calls(seal):
        raise ValueError("Strategy 9 failure source must bind its typed original entry graph")
    numbered_validation = named(trees[2], "validate_numbered_failure")
    if "validate_witness" not in calls(numbered_validation):
        raise ValueError("Normalized numbered failure must preserve its original witness validation")
    named(trees[5], "submit_followthrough_exit_v4")
    if "seal_followthrough_rows" not in calls(trees[6]):
        raise ValueError("Strategy 9 durable commit lacks the witness seal")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()

_LEGACY_PROTECTION_FAMILIES = {
    ("order_management", "partial_target_completion"): "_complete_partial_target",
    ("order_management", "profit_pocket_transition"): "apply_profit_pocket_transition",
    ("order_management", "dynamic_stop_ratcheted"): "_ratchet_dynamic_protection",
    ("broker", "protected_exit_modified"): "_modify_existing_protected_exit",
    ("broker", "protected_sliced_exit_modified"): "_modify_existing_protected_exit",
    ("order_management", "entry_acquisition_frozen_before_exit"):
        "_cancel_pending_acquisition_before_exit",
}
_REDUNDANT_MODIFY_SUMMARIES = {
    ("broker", "profit_target_replaced"): "_replace_existing_profit_targets",
    ("broker", "protective_stop_replaced"): "_replace_protective_stop",
}


def certify_strategy_one_assignment_event_unreachable(
    *, controller_path: Path, runtime_path: Path,
    strategy_path: Path = _STRATEGY_ONE_RUNTIME,
    execution_path: Path = _STRATEGY_ONE_EXECUTION,
) -> str:
    """Bind the event-free fixed assignment save to the sparse-only call path.

    This excludes the legacy activity event, not the need to cold-recover the
    initial numbered assignment and its configuration from typed storage.
    """
    paths = (controller_path, runtime_path, strategy_path, execution_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    controller, runtime, strategy, execution = map(ast.parse, sources)

    def method(tree: ast.Module, cls: str, name: str) -> ast.AST:
        owners = [node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == cls]
        matches = [node for node in owners[0].body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == name] if len(owners) == 1 else []
        if len(matches) != 1:
            raise ValueError(f"Strategy 1 assignment event route changed: {name}")
        return matches[0]

    assigned = [node for node in strategy.body if isinstance(node, ast.ClassDef)
                and node.name == "AssignedStrategyOne"]
    if (len(assigned) != 1 or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"on_order_group_update", "on_market_signal"}
            for node in assigned[0].body)):
        raise ValueError("Strategy 1 gained a legacy assignment update handler")
    fixed = method(controller, "ReplayRunController", "_run_strategy_one_fixed_days")
    initialise = method(controller, "ReplayRunController", "_initialize_runtime")
    initial_saves = [node for node in ast.walk(initialise)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "persist_strategy_assignments"]
    if (len(initial_saves) != 1 or not any(keyword.arg == "record_events"
            and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in initial_saves[0].keywords)):
        raise ValueError("Strategy 1 initial assignment save can emit activity")
    runner = [node for node in ast.walk(fixed) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name)
              and node.func.id == "run_certified_strategy_one_session"]
    session = [node for node in execution.body
               if isinstance(node, ast.AsyncFunctionDef)
               and node.name == "run_certified_strategy_one_session"]
    if (len(runner) != 1 or len(session) != 1
            or any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"process_market_signal",
                                          "process_strategy_observation",
                                          "process_account_strategy_observation",
                                          "persist_strategy_assignments"}
                   for node in ast.walk(session[0]))):
        raise ValueError("Strategy 1 sparse runner can reach assignment activity")
    emitter = method(runtime, "TradingRuntime", "persist_strategy_assignments")
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "append_many" for node in ast.walk(emitter)):
        raise ValueError("Assignment activity emitter changed")
    # Fill/state callbacks persist only if the strategy supplies this legacy
    # handler. The numbered strategy above deliberately does not.
    for name in ("_on_order_group_fill", "_on_order_group_state"):
        callback = method(runtime, "TradingRuntime", name)
        saves = [node for node in ast.walk(callback)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "_persist_strategy_assignments"]
        if len(saves) != 1 or not any(isinstance(node, ast.If)
                and ast.unparse(node.test) == "handler is not None"
                and saves[0] in ast.walk(node) for node in ast.walk(callback)):
            raise ValueError("Strategy 1 fill callback can emit assignment activity")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_portfolio_request_unreachable(
    *, runtime_path: Path, portfolio_path: Path,
    contract_path: Path = _STRATEGY_ONE_CONTRACT,
) -> str:
    """Prove the legacy revision-41 deferred-request cleanup cannot run at 1."""
    paths = (runtime_path, portfolio_path, contract_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    runtime, portfolio, contract = (ast.parse(source) for source in sources)
    numbers = [node.value.value for node in contract.body
               if isinstance(node, ast.Assign) and len(node.targets) == 1
               and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id == "STRATEGY_NUMBER"
               and isinstance(node.value, ast.Constant)]
    if numbers != [1]:
        raise ValueError("Strategy 1 number no longer precedes deferred-request cleanup")
    runtime_classes = [node for node in runtime.body if isinstance(node, ast.ClassDef)
                       and node.name == "TradingRuntime"]
    portfolio_classes = [node for node in portfolio.body if isinstance(node, ast.ClassDef)
                         and node.name == "PortfolioManagementEngine"]
    if len(runtime_classes) != 1 or len(portfolio_classes) != 1:
        raise ValueError("Portfolio request source owners changed")
    execute = [node for node in runtime_classes[0].body
               if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_intents"]
    withdraw = [node for node in portfolio_classes[0].body
                if isinstance(node, ast.FunctionDef)
                and node.name == "withdraw_invalidated_requests"]
    calls = [node for node in ast.walk(runtime)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "withdraw_invalidated_requests"]
    emitters = [node for node in ast.walk(portfolio)
                if isinstance(node, ast.Constant) and node.value == "portfolio_request"]
    if (len(execute) != 1 or len(withdraw) != 1 or len(calls) != 1
            or calls[0] not in ast.walk(execute[0])
            or len(emitters) != 1 or emitters[0] not in ast.walk(withdraw[0])):
        raise ValueError("Portfolio request has another or missing route")
    guards = [node for node in ast.walk(execute[0]) if isinstance(node, ast.If)
              and calls[0] in ast.walk(node)]
    if (len(guards) != 1 or ast.unparse(guards[0].test) !=
            "self.config.strategy_revision >= 41 and hasattr(self.strategy, "
            "'assignments') and self.portfolio.has_pending_entry_requests(account_id)"):
        raise ValueError("Portfolio request is not revision-41 guarded")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_legacy_protection_unreachable(
    *, oms_path: Path, runtime_path: Path,
    contract_path: Path = _STRATEGY_ONE_CONTRACT,
) -> str:
    """Prove the numbered strategy bypasses legacy OMS protection and exits."""
    paths = (oms_path, runtime_path, contract_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    oms, runtime, contract = (ast.parse(source) for source in sources)
    contract_values = {node.targets[0].id: node.value.value
                       for node in contract.body if isinstance(node, ast.Assign)
                       and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                       and isinstance(node.value, ast.Constant)
                       and node.targets[0].id in {"STRATEGY_ID", "STRATEGY_NUMBER"}}
    if contract_values != {"STRATEGY_ID": "early-squeeze-strategy", "STRATEGY_NUMBER": 1}:
        raise ValueError("Strategy 1 legacy protection identity changed")
    oms_classes = [node for node in oms.body if isinstance(node, ast.ClassDef)
                   and node.name == "OrderManagementEngine"]
    runtime_classes = [node for node in runtime.body if isinstance(node, ast.ClassDef)
                       and node.name == "TradingRuntime"]
    if len(oms_classes) != 1 or len(runtime_classes) != 1:
        raise ValueError("Strategy 1 OMS construction identity changed")
    imports = [node for node in oms.body if isinstance(node, ast.ImportFrom)
               and node.module == "src.trading_runtime.strategy_one_contract"]
    initializers = [node for node in oms_classes[0].body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__"]
    if (len(imports) != 1 or {alias.name for alias in imports[0].names}
            != {"STRATEGY_ID", "STRATEGY_NUMBER"} or len(initializers) != 1
            or any(sum(isinstance(node, ast.Assign)
                       and ast.unparse(node) == f"self.{field} = {field}"
                       for node in ast.walk(initializers[0])) != 1
                   for field in ("strategy_id", "strategy_revision"))):
        raise ValueError("Strategy 1 OMS identity binding changed")
    constructors = [node for node in ast.walk(runtime_classes[0])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "OrderManagementEngine"]
    if len(constructors) != 1:
        raise ValueError("Strategy 1 OMS constructor changed")
    keyword_values = {keyword.arg: ast.unparse(keyword.value)
                      for keyword in constructors[0].keywords}
    if (keyword_values.get("strategy_id") != "config.strategy_id"
            or keyword_values.get("strategy_revision") != "config.strategy_revision"):
        raise ValueError("Strategy 1 identity is not forwarded to OMS")
    numbered_proof = _certify_numbered_identity()
    guard = "if is_numbered_fixed_strategy(self.strategy_id, self.strategy_revision):"
    returns = {
        "_complete_partial_target": "return False",
        "apply_profit_pocket_transition": "return []",
        "_ratchet_dynamic_protection": "return",
        "_modify_existing_protected_exit": "return None",
        "_cancel_pending_acquisition_before_exit": "return",
    }
    for family, name in _LEGACY_PROTECTION_FAMILIES.items():
        methods = [node for node in oms_classes[0].body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == name]
        emitters = [node for node in ast.walk(oms)
                    if isinstance(node, ast.Constant) and node.value == family[1]]
        body = methods[0].body if len(methods) == 1 else []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        if (len(methods) != 1 or len(emitters) != 1
                or emitters[0] not in ast.walk(methods[0])
                or not body or not isinstance(body[0], ast.If)
                or ast.unparse(body[0]).split("\n", 1)[0] != guard
                or len(body[0].body) != 1
                or ast.unparse(body[0].body[0]) != returns[name]):
            raise ValueError(f"Strategy 1 legacy OMS event may be reachable: {family}")
    for family, name in _REDUNDANT_MODIFY_SUMMARIES.items():
        methods = [node for node in oms_classes[0].body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == name]
        if len(methods) != 1:
            raise ValueError("Strategy 1 modification route changed")
        method = methods[0]
        emitters = [node for node in ast.walk(oms)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_record" and len(node.args) >= 2
                    and all(isinstance(arg, ast.Constant) for arg in node.args[:2])
                    and tuple(arg.value for arg in node.args[:2]) == family]
        guards = [node for node in ast.walk(method) if isinstance(node, ast.If)
                  and len(emitters) == 1 and emitters[0] in ast.walk(node)]
        acknowledgements = [node for node in ast.walk(method)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr ==
                            "_record_strategy_one_modify_acknowledgement"]
        effective = [node for node in ast.walk(method)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "_record_protection"
                     and any(keyword.arg == "phase"
                             and isinstance(keyword.value, ast.Constant)
                             and keyword.value.value == "effective"
                             for keyword in node.keywords)
                     and any(keyword.arg == "amendment_intent"
                             and isinstance(keyword.value, ast.Name)
                             and keyword.value.id == "intent"
                             for keyword in node.keywords)]
        if (len(emitters) != 1 or len(guards) != 1
                or ast.unparse(guards[0].test) !=
                "not is_numbered_fixed_strategy(self.strategy_id, self.strategy_revision)"
                or len(acknowledgements) != 1 or len(effective) != 1
                or not acknowledgements[0].lineno < effective[0].lineno
                       < emitters[0].lineno):
            raise ValueError(f"Strategy 1 modification summary may be reachable: {family}")
    return sha256(json.dumps({"version": 1, "numbered_identity": numbered_proof, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_fixed_rebalance_unreachable(
    *, runtime_path: Path, portfolio_path: Path,
    intent_path: Path = _STRATEGY_ONE_INTENT,
) -> str:
    """Bind the Strategy 1 capital guard to Portfolio's rebalance condition."""
    paths = (runtime_path, portfolio_path, intent_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    runtime, portfolio, intent = (ast.parse(source) for source in sources)

    def method(tree: ast.AST, owner: str, name: str, kind: type) -> ast.AST:
        classes = [node for node in getattr(tree, "body", ())
                   if isinstance(node, ast.ClassDef) and node.name == owner]
        matches = [node for node in classes[0].body
                   if isinstance(node, kind) and node.name == name] if len(classes) == 1 else []
        if len(matches) != 1:
            raise ValueError("Strategy 1 rebalance exclusion source changed")
        return matches[0]

    execute = method(runtime, "TradingRuntime", "_execute_intents", ast.AsyncFunctionDef)
    expected_guard = (
        "if self.config.mode == RunMode.BACKTEST and "
        "self.config.strategy_id == STRATEGY_ID and "
        "(self.config.strategy_revision == STRATEGY_NUMBER):\n"
        "    from .strategy_one_intent import require_no_replacement_capital, "
        "require_strategy_one_actions\n"
        "    require_no_replacement_capital(evaluation.intents)\n"
        "    require_strategy_one_actions(evaluation.intents)"
    )
    if (len(execute.body) < 2
            or ast.unparse(execute.body[0]) !=
            "from .strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER"
            or ast.unparse(execute.body[1]) != expected_guard):
        raise ValueError("Strategy 1 replacement guard is not before Portfolio routing")
    helpers = [node for node in intent.body if isinstance(node, ast.FunctionDef)
               and node.name == "require_no_replacement_capital"]
    if (len(helpers) != 1 or len(helpers[0].body) != 2
            or ast.unparse(helpers[0].body[1]) !=
            "if any((intent.capital_request is not None and "
            "intent.capital_request.allow_replacement for intent in intents)):\n"
            "    raise ValueError('Strategy 1 cannot request replacement capital')"):
        raise ValueError("Strategy 1 replacement guard no longer rejects replacement")
    rebalance = method(portfolio, "PortfolioManagementEngine", "_propose_rebalance",
                       ast.FunctionDef)
    if (len(rebalance.body) < 3
            or ast.unparse(rebalance.body[0]) != "request = intent.capital_request"
            or ast.unparse(rebalance.body[2]) !=
            "if request is None or not request.allow_replacement or "
            "(not bool(mandate.get('allow_replacement', False))):\n"
            "    return None"
            or sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "_propose_rebalance"
                   for node in ast.walk(portfolio)) != 1):
        raise ValueError("Portfolio rebalance has another or unguarded route")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_fixed_broker_stream_unreachable(
    *, controller_path: Path, runtime_path: Path, oms_path: Path,
    broker_path: Path = _SIMULATED_BROKER,
) -> str:
    """Prove simulated Backtest cannot enter the IBKR websocket emitter."""
    paths = (controller_path, runtime_path, oms_path, broker_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    controller, runtime, oms, broker = tuple(ast.parse(source) for source in sources)
    simulated = [node for node in broker.body if isinstance(node, ast.ClassDef)
                 and node.name == "SimulatedBrokerAdapter"]
    if (len(simulated) != 1 or simulated[0].bases
            or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name in {"__getattr__", "__getattribute__",
                                     "stream_broker_messages"}
                   for node in ast.walk(simulated[0]))
            or any(isinstance(node, ast.Constant)
                   and node.value == "stream_broker_messages"
                   for node in ast.walk(simulated[0]))):
        raise ValueError("Simulated broker transport absence is unproven")
    initializers = [node for node in ast.walk(controller)
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "_initialize_runtime"]
    if len(initializers) != 1:
        raise ValueError("Backtest broker construction is unproven")
    broker_assignments = [node for node in ast.walk(initializers[0])
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name)
                                  and target.id == "broker"
                                  for target in node.targets)]
    runtimes = [node for node in ast.walk(initializers[0])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "TradingRuntime"]
    if (len(broker_assignments) != 1 or len(runtimes) != 1
            or not isinstance(broker_assignments[0].value, ast.Call)
            or not isinstance(broker_assignments[0].value.func, ast.Name)
            or broker_assignments[0].value.func.id != "SimulatedBrokerAdapter"
            or len(runtimes[0].args) < 2
            or not isinstance(runtimes[0].args[1], ast.Name)
            or runtimes[0].args[1].id != "broker"):
        raise ValueError("Backtest does not forward its simulated broker unchanged")
    starts = [node for node in ast.walk(runtime)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "_consume_broker_stream"]
    guards = [node for node in ast.walk(runtime) if isinstance(node, ast.If)
              and ast.unparse(node.test) ==
              "hasattr(self.broker, 'stream_broker_messages')"]
    if (len(starts) != 1 or len(guards) != 1
            or starts[0] not in ast.walk(guards[0])
            or sum(isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "on_broker_message"
                   for node in ast.walk(runtime)) != 1):
        raise ValueError("OMS websocket consumption is not exclusively guarded")
    emitters = [node for node in ast.walk(oms)
                if isinstance(node, ast.Constant)
                and node.value == "broker_execution"]
    methods = [node for node in ast.walk(oms)
               if isinstance(node, ast.AsyncFunctionDef)
               and node.name == "on_broker_message"]
    if (len(emitters) != 1 or len(methods) != 1
            or emitters[0] not in ast.walk(methods[0])):
        raise ValueError("Broker execution has another or missing emitter")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_v4_projection(
    *, controller_source: Path | None = None,
    indirect_sources: tuple[Path, ...] = _INDIRECT_SOURCES,
) -> str:
    """Reject any indirect emitter without a proven normalized V4 projection.

    The direct controller certificate already checks fixed-only reachability.
    This separate indirect inventory binds the runtime/OMS/portfolio sources;
    it cannot be replaced by observing one Backtest's emitted records.
    """
    direct = (certify_direct_v3_projection(source_path=controller_source)
              if controller_source is not None else certify_direct_v3_projection())
    families, dynamic = indirect_journal_inventory(indirect_sources)
    if dynamic:
        raise ValueError(f"V4 indirect journal emitter identity is dynamic: {dynamic}")
    unreachable: set[tuple[str, str]] = set()
    unreachable_proof = ""
    if _FIXED_UNREACHABLE_ADAPTIVE_REPRICE in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 adaptive skip lacks fixed-runtime source authority")
        proof_args = {
            "oms_path": sources_by_name["order_management.py"],
            "runtime_path": sources_by_name["runtime.py"],
        }
        if controller_source is not None:
            proof_args["controller_path"] = controller_source
        unreachable_proof = certify_fixed_adaptive_reprice_unreachable(
            **proof_args)
        unreachable.add(_FIXED_UNREACHABLE_ADAPTIVE_REPRICE)
    if ("execution", "broker_execution") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 broker execution lacks fixed-runtime source authority")
        websocket_proof = certify_fixed_broker_stream_unreachable(
            controller_path=controller_source or Path(__file__).with_name(
                "replay_run_service.py"),
            runtime_path=sources_by_name["runtime.py"],
            oms_path=sources_by_name["order_management.py"])
        unreachable_proof += websocket_proof
        unreachable.add(("execution", "broker_execution"))
    if ("portfolio_management", "portfolio_rebalance") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "portfolio.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 rebalance lacks fixed-runtime source authority")
        unreachable_proof += certify_fixed_rebalance_unreachable(
            runtime_path=sources_by_name["runtime.py"],
            portfolio_path=sources_by_name["portfolio.py"])
        unreachable.add(("portfolio_management", "portfolio_rebalance"))
    if set(families) & (_LEGACY_PROTECTION_FAMILIES.keys()
                       | _REDUNDANT_MODIFY_SUMMARIES.keys()):
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 legacy protection lacks fixed-runtime source authority")
        unreachable_proof += certify_strategy_one_legacy_protection_unreachable(
            oms_path=sources_by_name["order_management.py"],
            runtime_path=sources_by_name["runtime.py"])
        unreachable.update(set(families) & _LEGACY_PROTECTION_FAMILIES.keys())
        unreachable.update(set(families) & _REDUNDANT_MODIFY_SUMMARIES.keys())
    if ("portfolio_management", "portfolio_request") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "portfolio.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 portfolio request lacks source authority")
        unreachable_proof += certify_strategy_one_portfolio_request_unreachable(
            runtime_path=sources_by_name["runtime.py"],
            portfolio_path=sources_by_name["portfolio.py"])
        unreachable.add(("portfolio_management", "portfolio_request"))
    if ("strategy", "strategy_assignment_state") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "runtime.py" not in sources_by_name):
            raise ValueError("Strategy 1 assignment event lacks runtime source authority")
        unreachable_proof += certify_strategy_one_assignment_event_unreachable(
            controller_path=controller_source or Path(__file__).with_name(
                "replay_run_service.py"),
            runtime_path=sources_by_name["runtime.py"])
        unreachable.add(("strategy", "strategy_assignment_state"))
    supported = _V3_PROJECTED | _COMMON_TYPED | _V4_ADDITIONS
    unsupported = sorted(set(families) - supported - unreachable)
    if unsupported:
        raise ValueError(f"V4 indirect emitters lack typed projection: {unsupported}")
    evidence = tuple((path.name, sha256(path.read_bytes()).hexdigest())
                     for path in indirect_sources)
    return sha256(json.dumps({
        "version": 1, "direct": direct, "families": families,
        "sources": evidence, "unreachable_proof": unreachable_proof,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_recent_bos_entry_source(*, source_path: Path | None = None,
                                    static_path: Path | None = None,
                                    adapter_path: Path | None = None,
                                    coordinator_path: Path | None = None,
                                    intent_path: Path | None = None,
                                    commit_path: Path | None = None) -> str:
    """Bind Strategy 12's inclusive BOS clocks to vector and sequential routes."""
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (source_path or runtime_root / "strategy_recent_bos_entry.py",
             static_path or Path(__file__).with_name("backtest_strategy_one_static_gate.py"),
             adapter_path or Path(__file__).with_name("backtest_strategy_one_stateful.py"),
             coordinator_path or Path(__file__).with_name("backtest_strategy_one_coordinator.py"),
             _STRATEGY_ONE_EXECUTION, intent_path or _STRATEGY_ONE_INTENT,
             runtime_root / "arte_strategy_one_entry_journal.py",
             runtime_root / "runtime.py", commit_path or runtime_root / "arte_journal_commit_v4.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    constants = [n for n in trees[0].body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "MAX_BOS_ENTRY_AGE_MS" for t in n.targets)]
    if len(constants) != 1 or ast.unparse(constants[0].value) != "30000":
        raise ValueError("Strategy 12 recent BOS age bound changed")
    expected = {'recent_bos_entry': ("if type(boundary_ms) is not int or not 0 < boundary_ms <= 57600000 or boundary_ms % 100:\n    raise ValueError('Recent BOS entry needs a completed 100ms boundary')", 'if bos_break_boundary_ms is None:\n    return False', "if type(bos_break_boundary_ms) is not int or not 0 < bos_break_boundary_ms <= boundary_ms or bos_break_boundary_ms % 1000:\n    raise ValueError('Recent BOS entry needs a causal completed 1s break')", 'return boundary_ms - bos_break_boundary_ms <= MAX_BOS_ENTRY_AGE_MS'), 'recent_bos_entry_mask': ('boundaries = np.asarray(boundaries_ms)', 'breaks = np.asarray(bos_break_boundaries_ms)', "if boundaries.ndim != 1 or breaks.shape != boundaries.shape or boundaries.dtype.kind not in 'iu' or (breaks.dtype.kind not in 'iu') or np.any(boundaries <= 0) or np.any(boundaries > 57600000) or np.any(boundaries % 100) or np.any(breaks < 0) or np.any(breaks > boundaries) or np.any(breaks % 1000):\n    raise ValueError('Recent BOS entry needs aligned causal completed clocks')", 'age = boundaries.astype(np.int64, copy=False) - breaks.astype(np.int64, copy=False)', 'return (breaks > 0) & (age <= MAX_BOS_ENTRY_AGE_MS)')}
    for name, statements in expected.items():
        functions = [n for n in trees[0].body if isinstance(n, ast.FunctionDef) and n.name == name]
        actual = tuple(ast.unparse(n) for n in functions[0].body
                       if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                               and isinstance(n.value.value, str))) if len(functions) == 1 else ()
        if actual != statements:
            raise ValueError("Strategy 12 recent BOS completed-clock or inclusive bound changed")
    static_routes = [n for n in ast.walk(trees[1]) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "strategy_number in (12, 13, 14, 15, 16, 17, 18, 19)"]
    if len(static_routes) != 1 or not all(value in ast.unparse(static_routes[0]) for value in
            ("recent_bos_entry_mask(boundaries, break_boundaries)",
             "fact.bos_break_boundary_ms or 0", "reasons |= (~recent).astype(np.uint8) * RECENT_BOS_REQUIRED")):
        raise ValueError("Strategy 12 recent BOS vector gate is not exclusively routed")
    adapter_routes = [n for n in ast.walk(trees[2]) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "strategy_number in (12, 13, 14, 15, 16, 17, 18, 19) and (not recent_bos_entry(boundary_ms=fact.boundary_ms, bos_break_boundary_ms=fact.bos_break_boundary_ms))"]
    if len(adapter_routes) != 1 or ast.unparse(adapter_routes[0].body[0]) != "return StrategyOneEntryDecision('recent_supported_bos_required')":
        raise ValueError("Strategy 12 recent BOS sequential adapter is not exclusively routed")
    coordinator_calls = [n for n in ast.walk(trees[3]) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == "propose_certified_strategy_one_entry"]
    if len(coordinator_calls) != 1 or not any(k.arg == "strategy_number" and ast.unparse(k.value) == "strategy_number" for k in coordinator_calls[0].keywords):
        raise ValueError("Strategy 12 coordinator must thread authoritative numbered identity")
    execution_calls = [n for n in ast.walk(trees[4]) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name) and n.func.id == "run_strategy_one_proposals"]
    if len(execution_calls) != 1 or not any(k.arg == "strategy_number" and ast.unparse(k.value) == "config.strategy_revision" for k in execution_calls[0].keywords):
        raise ValueError("Strategy 12 execution must thread certified revision")
    intent_routes = [n for n in ast.walk(trees[5]) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "proposal.strategy_number in (12, 13, 14, 15, 16, 17, 18, 19)"]
    if len(intent_routes) != 1 or "if not recent_bos_entry(boundary_ms=proposal.boundary_ms, bos_break_boundary_ms=proposal.bos_break_boundary_ms):" not in ast.unparse(intent_routes[0]):
        raise ValueError("Strategy 12 intent authority must reject forged stale BOS")
    # Persistence, cold entry evidence and runtime admission reuse the guarded
    # factory; raw normalized rows reuse the same pure eligibility authority.
    for tree, names in ((trees[6], ("project_strategy_one_entry_evidence", "load_committed_strategy_one_entry_page")),
                        (trees[7], ("submit_strategy_one_proposal",))):
        for name in names:
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
            runtime_route = name == "submit_strategy_one_proposal"
            if len(functions) != 1 or not any(isinstance(n, ast.Call) and (
                    isinstance(n.func, ast.Attribute) and n.func.attr == "_strategy_one_entry_intent"
                    if runtime_route else isinstance(n.func, ast.Name)
                    and n.func.id == "strategy_one_entry_intent") for n in ast.walk(functions[0])):
                raise ValueError("Strategy 12 persisted entry authority factory route changed: " + name)
            if runtime_route:
                wrappers = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                            and n.name == "_strategy_one_entry_intent"]
                expected = _RISING_MOMENTUM_REVIEWED_AST["trading_runtime/runtime.py"]["_strategy_one_entry_intent"]
                if len(wrappers) != 1 or sha256(ast.unparse(wrappers[0]).encode()).hexdigest() != expected:
                    raise ValueError("Strategy 12 runtime entry wrapper differs from reviewed authority")
    raw_routes = [n for n in ast.walk(trees[8]) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "row['strategy_number'] in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31)"]
    if len(raw_routes) != 1 or "if not recent_bos_entry(boundary_ms=row['boundary_ms'], bos_break_boundary_ms=row['bos_break_boundary_ms']):" not in ast.unparse(raw_routes[0]):
        raise ValueError("Strategy 12 raw normalized entry authority must reject forged stale BOS")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()


# Reviewed Strategy 13 source observations, filter, submission and durability
# routes. Exact canonical AST seals bind the tested implementation; a later
# behavior change gets a new numbered release. Comments/line endings do not
# affect these seals. The run separately pins its complete backend fingerprint.
_RISING_MOMENTUM_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'BacktestMemoryJournal': 'fdc323d3f0ed7c5a25c4c364fe6e44e92aae4c46f930c6653a1f044d611e045c', 'mark_fenced': 'f2e22a2c8ff968e6dc81dcb1a988cfa811ed606072050f6efe5bf0a0024c211b', 'append_profit_giveback_exit': 'bc5260fc0458a1a25c50b6a664298a79dae65b1c3790a6a222f129e7863d3040', 'profit_giveback_exit_for_record': '961b81e4f28658ccfafcdeae95bac8c0c1aa4951f989a86cd819bab86212ed40',
                                        'append_followthrough_exit': '493ec00fe0160d0d628fb60109a7d0e3fa4a8c2ca09b8837ddcc5351834b103f',
                                        'append_strategy_one_intent': 'f548f30cd0889e513eee47f94ab06affe8ce2e17e12c2543e21eae8cd769e5f9',
                                        'append_strategy_one_protection_intent': 'f9f44f47daeb613231bae95393477e0f752e5c9dd78ee07c9d10baa6f8a128be'},
 'backend/backtest_market_plan_cache.py': {'selected_product_inventory_fingerprint': '2a24f97bf8d162e0d979c01fedbaba02de6e37cdaa0da48c482700c31211f355'},
 'backend/backtest_saved_source_authority.py': {'__module__': 'bb486b3cd6337d13ab5bfa9aea9e6614ab8e5a5a3bac558b06194cc240d8a5fc'},
 'backend/backtest_strategy_certified_price_break.py': {'__module__': '1111d36f019187db2127ab86fdf66f4365d6b178a9bc794c1bc88a162a309a95'},
 'backend/backtest_strategy_first_price_source.py': {'__module__': '8c711095ee81bbe5541a6c0a208f1380effdb057039976c43f8ffcee1afed268'},
 'backend/backtest_strategy_initial_momentum.py': {'__module__': '954446cab169240a183801637b4f61b27f90d45467b065ef428760d1ed5a48df'},
 'backend/backtest_strategy_initial_momentum_growth.py': {'__module__': '33bf35371d2216a5361e735959cdd1e48a65be3a5d8f04ec3d79cd277d199485'},
 'backend/backtest_strategy_initial_price_break.py': {'__module__': '6e48bc02aa9a734e70327db977531d967f85f469ed30208341768d3ed2701208'},
 'backend/backtest_strategy_initial_ten_percent.py': {'__module__': '73b8b04654cd8ebef2a8906908bac4c82fa06616fb4d50d19f73442f7f07f420'},
 'backend/backtest_strategy_one_configuration.py': {'__module__': '9dfb89129a3c60d8c4eefa1d36f89ef6bc5c624c4c8106e474c5606e783ceaff'},
 'backend/backtest_strategy_one_coordinator.py': {'run_strategy_one_proposals': '2b19931dc10b56e2d7630fef49a56fdabfae2caa25a75c56aaf5a6587df2a37c'},
 'backend/backtest_strategy_one_execution.py': {'run_certified_strategy_one_session': '44c936b5210500abd79e69d0d960d989405ed3a68717af5d99575b1742a3b30b',
                                                'run_strategy_one_fixed_session': '81c7eace24ec0199ff941a86b79aff182ffaad67c95000e245bece234042f917'},
 'backend/backtest_strategy_one_management.py': {'__init__': '15f49c5122419f46f18701be98492a8ea44500045f507eab3ac7b489a6fe6483', 'profit_arming_requests': '402901aa0edd41c85b81749af3af8dbe1cc58f480d4886eb1ecd795158eb5ff6', 'accept_profit_arming_references': '3887b6c277a19ddd561fe092695fdd895d5c6fd8616e434cfc644853767eafdd', '_validate_capture': '5b7ac922dd05b4f2e9b0e045125c589672ec4e219f25d652e900f9bf03f23b76',
                                                 'on_management': '5641a9a232689fbb3847afef3e5334bc564f1a9691f7d3b2ec926a70ce9d2f54',
                                                 'restore_state': 'ab345264866a57dd0dacd2c28276beeaa2a76b68ab5973673864e5ed11846757'},
 'backend/backtest_strategy_one_stateful.py': {'propose_certified_strategy_one_entry': '3d1f757e2845b029cdfe98b1ec0a8ed0213081268b27ef044de6b2ccf98ba9a1'},
 'backend/backtest_strategy_one_static_gate.py': {'compile_static_entry_gate': 'e6d2f747557dfbe005d492fa4a24ef671f47736961196cd1ca27ec2da438d0e5'},
 'backend/backtest_strategy_one_v7_interval_store.py': {'certify_v7_interval_plan': '3e5a6b0150e9225821f165971f4c2526d41c2d9934c50bdbaa9101c295c219aa'},
 'backend/backtest_strategy_rising_momentum.py': {'__module__': 'c0f4a1084b29088a1df63cdeb5c1b82b3d467fe2a657ee08ab37037bd6a6f245'},
 'backend/backtest_typed_projection.py': {'project_pending_backtest_v4_prefix': '482a77c7c499fb1733da9ce010506867c5e6972d06fe7a0b0c60cf5d81fbdf54'},
 'backend/backtest_typed_publisher.py': {'_drain': '8ad38093cc195b1cdba0e0d3337f1e7f7feca1e8fbea2977b15a82c6c60b6e76',
                                         '_prepare_batches': 'f6b5a12c2a87430456bf21c61ba4cf5cfc64b5682e0c15effcd98a6776515155',
                                         '_publish_terminal_v4': 'f9eabf5aca42abc249a30d7d164fc8689a56b16c284c2a8bb523d823a3932ce4',
                                         'bind_first_price_source': '230d3eb0d0da610f6b7e74e10d674fea2ce8cc53f579e4c7d837e9f6dfe2cd02'},
 'backend/backtest_v4_history.py': {'__module__': '9ed3f860d27d2b6f776d4b42c0e9225fc84a75744504fa9dce60dcdd1326bd23'},
 'backend/backtest_v4_saved_review.py': {'_saved_twenty_price_source': '54c832b11f14a37f6eae34a9b95db1869c9c6f8576b1986c541ff318728cd36a',
                                         '_terminal_attestation': '5a7f115e07e446453cc72d8ec9a2e09ee70c553588fdfc1d2ab679416d565e31'},
 'backend/historical_runtime_versions.py': {'__module__': '96941bdcb6b84439c3ba3f0d1fc0deb9cde238746088d1b284284aa0969857ec'},
 'backend/replay_run_service.py': {'_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482', '_confirm_profit_arming_checkpoint': 'ae4fa9304039b04e0fed10e84350052e4fd83435188b6551fe5cc16c4fc70994', '_run_strategy_one_fixed_days': '4d227d4c8cb56b3ab39d482cf5c3af2a2891d2487a4385e498baa8b289e1bae3',
                                   'backtest_preflight': 'd60aecbd6be87d19f5dcafea4c350974859a3253d9af6fa575190be14d5a1e83'},
 'trading_runtime/arte_backtest_definition.py': {'_reconstruct_backtest_definition': '5848ac310bcb92ba03e71eae9dc962f757191951fb77389c46eaa946779e86d0',
                                                 'reconstruct_backtest_definition_from_arte': 'f9f64dcd409d411db63d2057246b597e9a499df7b899f4afd46b708cd3f08f61',
                                                 'reconstruct_saved_review_definition_from_arte': '8ea00e67695146cbaee08d6825d4fd55eb9300e5ef16abcf21b98282658ddc2e'},
 'trading_runtime/arte_first_price_entry_v4.py': {'__module__': 'de00c87961b1ee2a1fee256c6d29932c745f18930ec0f6ecbd79c4165604c7fc'},
 'trading_runtime/arte_followthrough_failure_v4.py': {'_source_entry': '2e3eb76a2d8ade0055c9792254311e1af4f5ebde2933b226530c2b2c39f80b46',
                                            'seal_followthrough_rows': 'b86ba152f390b394536b69d9ff3d7503760d34db6a31eeb39dc9c547fe1f263b',
                                                      'validate_numbered_failure': 'f0615efb7981df1645233c2c52f7e45bedecd96e9fb6cb5da1702d065d20baad'},
 'trading_runtime/arte_initial_momentum_entry_v4.py': {'__module__': '955a6fb3390463e8c7eac4088c0fd3bc78e7324fc6c17829d18c014b0b1b041f'},
 'trading_runtime/arte_journal_commit_v4.py': {'verified_batch_predecessor': 'e7f52f08e28175674cf663dbde1b17abf7bf53513884f7d2289fbdd4dfe63ecf',
                                            '_load_verified_details_v4': 'b7e19ffdc7d3ac920e802e4199391166d4606f4e2705690eaba206101ac91cbd',
                                               '_publish_sealed_batch_v4': 'e708d6fcdcf1417b71c759630046f1107680d2547d9299ff7e68278bd3109881',
                                               '_publish_typed_batch_v4': '5d259d78bb9e492c4a36c0c10d8dcdd70c62d474efdada06683b7e9737d7f99f',
                                               '_validate_strategy_one_entry_link': 'b568eb5e08f971c5845592ed7c7828de45635f6910cd60dc63dc56baf0e4950f',
                                               'load_verified_commit_v4': '251a5494cd2f0a1fae07611bddae8414cbadc33dcff8018d371c5ea6f0b0dcb2',
                                               'load_verified_v4_prefix': '492e028bdedeb79943fa7430bac0e458a864e5061f8ac12feb50174f1298cb41',
                                               'load_writer_v4_snapshot_prefix': 'ca33712bca84d4658083a7ee861893974dd2a7578028203d09d56b859d917d8b',
                                               'publish_strategy_one_entry_batch_v4': '800a0ff809c6a13fd0fdfc1e8c07d6808b46ebbcbbad939535a5b35f20c68cc6',
                                               'publish_terminal_typed_batch_v4': '96dd1d05ed478620bb78d0b2c50f6c6858ed5530b97b533bbf389f1cc1d799cd'},
 'trading_runtime/arte_journal_compound_v4.py': {'__module__': '90bacc9a2c28335a1b33380f05e9d572a06ff0fce2fff2205647b363cc08b507',
                                                 '_publication_kwargs': 'e9cde80cad1d6cfb3cbd98b59bb86dc0963d512abf87288fb327dda2d9644551',
                                                 '_unit_children': 'ada8a6bd980c6b047f14d4721dc83ae03c3ef2c05a5ad72591c150de2519f56b',
                                                 'prepare_compound_v4_families': '3114ffc8feb88a70c0e8743e2e45c9c94c0f9156cd547eaa865b0d6a4d773562'},
 'trading_runtime/arte_journal_writer.py': {'_ProfitPublicationUnit': '7cf5b6eec9ddf368a1427d43fc5cfda7ae48b1efe661d66af970f160f82f2c72', 'submit_compound_v4': 'c58b7643ae10cebb22c097358cc876f42db608843a2b79a05854b10520d7caff', 'submit_profit_exit_v4': '0b1344d9735e100b5901ca3df69773968e838a738127d73df3510478c0e1c2fe', '_submit_profit_publication': 'ecd52f6c5f3791da63cf8ebf5e04ec562473f4685bc47fceef6fb4b1213a1c71',
                                            'V4StrategyOneEntryBatch': '2e44c0950e38ef417797537036765f189a8e5ebf81fb266f6356f659bf14bbd5',
                                            '_BrokerMatchSnapshotUnit': 'b9f57982ff159fc4c38acf5e2bea17e3edec5941ca5a3a339231949824982617',
                                            '_CampaignSnapshotUnit': '7d38bc41d265b2507a147da65bfa59c298fe0b77234ca611ede1c70367714a75',
                                            '_EvidenceSnapshotUnit': 'dd22c3b1bf5a3eac08d55fa86790b60e8cb854b7678c0b4866185c9208af400e',
                                            '_ManagerSnapshotUnit': '0fc3c933a83a2c48e0feb873cf3882e4f9ef89828e9543723469bb8facb7e18f',
                                            '_OmsObservationSnapshotUnit': 'f205cbfa584c3566500f0558ef3b08a44e8f07f56778cba9000a3667d7701001',
                                            '_TerminalBacktestUnit': 'f42ccbf195ea58f40dc4a78fa7494655b47b59558d7546191a21e3f5c0fa9f62',
                                            '_run': 'd06bdde7730acd66f4fac07f7abb1d3bd3e705b55de28eee43d50fcf38ae32a7',
                                            '_validate_checkpoint_price_source': '2cdc6c63fdaf1effa48c2e0e467d2582620e796f2ee4a383e297584ed39a6b74',
                                            'submit_broker_match_snapshot': 'ccb2ae1ec6caf555675db0b729e91636d06d35d408acde560f52e7056d7d6324',
                                            'submit_campaign_snapshot': 'fa09a5cf1c45b87aa873185b92267c21965b042ce0a216dfa2f0ec046ffdf85b',
                                            'submit_evidence_snapshot': '03c8628952e81825891a0f936db7a911b2984d20ef41772aeaeb9d0ff4a26674',
                                            'submit_manager_snapshot': 'ffaad9b45cb3bf23979d57c4814bd48f5ac62c57e1055da3ef1d345efa355c0f',
                                            'submit_oms_observation_snapshot': '79e7e068135f55029751ed1fd710b63ccdcb902825c972f73ac58e4a6bca25fe',
                                            'submit_terminal_backtest': '2cd75bbabb2057da72695abec3ca937c2d4370f6fe2391ea3612d378114c6344',
                                            'v4_journal_write_tables': '833098535aa1046d02c7b42b678a07ef267f576b983c0e752632ff3fb6f30a4d',
                                            'v4_storage_contracts': '7c6e09a22915461683c77514fd35e7004aa31298ce89619015975faa138b8528'},
 'trading_runtime/arte_profit_giveback_v4.py': {'__module__': '98b846f81be755631ac9e89fa4a652f0c43ef7e30ef19c813aeb51ab88ca8c82'},
 'trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'a1f47fd722dd0aae08c1e92016981d2e318ae8e629ca641962ed696d2b8138dd'},
 'trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '2f1153deac085dc4ba9a302b939b0fb68d93f5cf00441ec139e28617d30ef42a'},
 'trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'be102d6ec3aea5ce7be170c3f7d9fb8fc8d7fefe38403fd5022d6b7400d7f810'},
 'trading_runtime/arte_rising_momentum_entry_v4.py': {'__module__': 'f3250d9c2fbf8b64601f0f94ac1f4b0bbc4209c0aca5d7c06e52cd6391ac56bf'},
 'trading_runtime/arte_strategy_one_entry_journal.py': {'load_committed_strategy_one_entry_page': '4bbba450a90c56f3e27128fcee5182a6156749b6d7b1e5bf14094c0f0dd52992',
                                                        'load_committed_strategy_one_source': '2ca82ddc4f8d8c39073eea61c32b5d38979e04bf91d5eb9a981bb22e3082785e',
                                                        'project_strategy_one_entry_evidence': '81bf44b55d59cd49234ab6b69774d20ae1b85dbd56768ee613bc2a83cb64972e'},
 'trading_runtime/numbered_fixed_strategy.py': {'__module__': '4075ee85ce0b0f92a8403abb89d41b5378691db45973ec8c701b99f4d5cc82e9'},
 'trading_runtime/runtime.py': {'submit_followthrough_failure': '895610583125442598d5c23dc35297e668bfad6581cc5aaa6ae6c5c1eb72ae91', 'submit_profit_giveback': 'aaa05ace3b283c57bf949b74aaf336a5971c1f91a4947815d50f5efd6accd255', '_execute_intents': '84508aec84c4ad2ece79f428fca4ae7c0c3abbf0b9d664b2307d558c712ad021',
                                '_strategy_one_entry_intent': 'e1962f1f19f9466b8bf551988c26ac37c8cfa52cdc37581c28c623d934f399aa',
                                'bind_strategy_one_price_source': '2d53ed89b01a63e85dbc5840ecb2583ee5d80a09f3ece990cd3db1984fd358a2',
                                'submit_strategy_one_proposal': '5632beaf79d5438c1ed45b746a6a880bcc4bb08bbfafbc4204a0474e05b5535e'},
 'trading_runtime/strategy_followthrough_exit.py': {'__module__': 'ed1667d3b1b76f505e92b8e9498c8d9214dd2f11d9417a4aa0fe97261e8c38c9'},
 'trading_runtime/strategy_initial_momentum_growth.py': {'__module__': '68d66854b639e67a5d3734aaaf1a61ce015963d6841746a59c4ad85390761606'},
 'trading_runtime/strategy_initial_price_break.py': {'__module__': '5279377acee015b28239ecd1949657fb1ce66731835a6190b686bf54a38ce3ce'},
 'trading_runtime/strategy_initial_strong_momentum.py': {'__module__': '65c6021a7a7c287682a502989fe03b638ee6aaa6ae1488e9a323a0e4c75f4c06'},
 'trading_runtime/strategy_initial_ten_percent.py': {'__module__': '086a330212aa01c2ddf70bdb8ddb2db70654f4b851662977ea86ec307be18be2'},
 'trading_runtime/strategy_one_broker_match_snapshot.py': {'__module__': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262'},
 'trading_runtime/strategy_one_campaign_snapshot.py': {'__module__': 'c0dc6663aeb6c5d4f658056f24830a22823daeb8c3f03a0426c9a2f4bb6b56c2'},
 'trading_runtime/strategy_one_evidence_snapshot.py': {'__module__': 'b231921b43f4b332e4e0d5141aa8f4920d9cbcc2b55ef8ce8d8f12f003415a75'},
 'trading_runtime/strategy_one_intent.py': {'strategy_one_entry_intent': 'bca3aaf81302ccc413e61fc5d5b33905b376b93de4fe334dbf84036ed5a83873'},
 'trading_runtime/strategy_one_management_snapshot.py': {'_project_manager_snapshot_scalar': '9ce610aec43130d76ffa46ec3427334f44e81e688acc7ce9b505dee9f520e7b1',
                                                         'attach_committed_momentum_sources': '7bb5dde1ff3d58454e699fd47660b1c58eb5d6de5a8c0a25a1452250c1b75b99',
                                                         'load_attested_manager_snapshot': 'b7334c9c67638563a9cacdeb2376cabb1a376a372eac47389f24f5bf19602a3e',
                                                         'project_manager_snapshot': '271108b727442d6768fb660eee963d6d6c0b81d0578935374d4d2f9c4417c080',
                                                         'publish_manager_snapshot': '04aaa35af64ca161463ab2107ee92d40753b17b2f71b75d91a9ce95539410364',
                                                         'restore_manager_snapshot': 'f6f786343523ee899d73e9c9f561e9b0815f35208625db374325e523d59e6f17'},
 'trading_runtime/strategy_one_oms_observation_snapshot.py': {'__module__': 'd8f2ad09022bb5af10c3257113a604b32c3d6c0e2a263e8eb9c0a0d45731033b'},
 'trading_runtime/strategy_persistent_risk_failure.py': {'__module__': '62bbdbcc400315b7c63a3b3260dbaae0247cc128cadd26969075deacd61a3e52'},
 'trading_runtime/strategy_premarket_quarter_risk_failure.py': {'__module__': '87a7e1941a30e09f0e3b463187d9914f8389186a528d9f706ec76b5e6ea529b9'},
 'trading_runtime/strategy_registry.py': {'initialize_numbered_fixed_strategies': '8db8a0e8274b0d2121b0717799436006681546413cc18d086f925600ae9e76f8',
                                          'installed_numbered_fixed_strategy_numbers': '43b186df8d4ff46a5fbe9258e0949853a1554225dca0e5e10a049e65ed21bc63',
                                          'numbered_strategy_parent': '24095acc5b386d98ba30ba47628cb71ff43fbe66ae78ab90595df2e3bf1ab487'},
 'trading_runtime/strategy_rising_momentum_entry.py': {'__module__': '26f5e82b33a5e7e4126fd703d9748ca3ee14b3696df4f6b9eddb79db96e05ea7'},
 'trading_runtime/strategy_rising_momentum_witness.py': {'__module__': '6b02c83e2c8836fec80dec8541632ed4c8a67836ae0de18620da21fdcd97f13e'},
 'trading_runtime/strategy_strong_ten_second_momentum.py': {'__module__': '70071f8696a3675e4a7344328d65327a84b539cb7e03f09c4fae49542a74dba5'},
 'trading_runtime/strategy_thirty_release.py': {'__module__': '049384140302bd83801e7b16447263c1847aa9ff7d2a4853bea5eb10aad84ea2'},
 'trading_runtime/strategy_twenty_eight_release.py': {'__module__': '0cccc9b6115abd744a09d760482f35a317f0d1f795b70fd580a7c9c1730fe6a8'},
 'trading_runtime/strategy_twenty_five_release.py': {'__module__': '1bbba29e4542af0fbe42ef7d07ebe852926039910c02d0680d1ad429def83f3a'},
 'trading_runtime/strategy_twenty_four_release.py': {'__module__': '7913d809cbe160b2d2018c63f4749ee2dd085bc4a1f6fa8412dde9c5168f5859'},
 'trading_runtime/strategy_twenty_nine_release.py': {'__module__': '58f134b9c6f17f5448d536a00779cddf32a4a43d172edeed0137fe02e348e0e7'},
 'trading_runtime/strategy_twenty_one_release.py': {'__module__': '0255b201419a032c712acf7de0fb5c45f1fe78d502d47b64c4c247031ab35679'},
 'trading_runtime/strategy_twenty_release.py': {'__module__': 'ec84c03c9e2b5d314be5b77204fa27904285123db8c54563459fa19a9724d281'},
 'trading_runtime/strategy_twenty_seven_release.py': {'__module__': '25b56cd9b46abc53eda393573a8a0ceca991e9037eb6869e0ab17b51192d2348'},
 'trading_runtime/strategy_twenty_six_release.py': {'__module__': '7344cb7fcdcb6cd3f6e10aea1400fe4b8e9e721e11294eab402d63576e901440'},
 'trading_runtime/strategy_twenty_three_release.py': {'__module__': 'e43951d61064e390078f9ac25d78eca0c000e0d33b28c54e663b71f644511c4f'},
 'trading_runtime/strategy_twenty_two_release.py': {'__module__': 'dfd5e4ccca3a285d278649a3374412e24638e29e62f57cf62f3db64c902d830f'},
 'trading_runtime/strategy_zero_regime_risk_failure.py': {'__module__': '2cca428561093253b73728c7c52a41b9e91dcf622f28fdd7ca93bb66660e8cd1'}}


def certify_rising_momentum_entry_source(*, source_overrides: dict[str, Path] | None = None) -> str:
    """Fail closed when any reviewed Strategy 13 source or authority route changes.

    Covers exact producer attempt/key/Float64 reads, necessary-condition I/O
    pruning, scalar/native agreement, guarded Portfolio admission, exact two
    normalized companions, direct/compound commit seals and cold manager joins.
    Test-only path overrides allow mutations without changing installed files.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(_RISING_MOMENTUM_REVIEWED_AST):
        raise ValueError("Strategy 13 source override is outside reviewed authority")
    root = Path(__file__).parents[1]
    observations = []
    for relative, expected in _RISING_MOMENTUM_REVIEWED_AST.items():
        path = overrides.get(relative, root / relative)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError("Strategy 13 source cannot be parsed: " + relative) from exc
        for name, digest in expected.items():
            nodes = ([tree] if name == "__module__" else [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and node.name == name])
            if (len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest):
                raise ValueError("Strategy 13 reviewed source authority changed: " + relative + ":" + name)
        observations.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observations, separators=(",", ":")).encode()).hexdigest()


_SESSION_EXIT_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'append_numbered_session_exit_intent': 'bbe645544d6990d293b4c7eb85dacf1895bb935712f7082c4b3fbf36f1d98f70'},
 'trading_runtime/numbered_fixed_strategy.py': {'_SESSION_EXIT_REASONS': 'b37fa83744bb0a9cc1c2d4855f7b778b3ffc8667b20fd8c53e11e79958673fd2',
                                                'numbered_session_exit_reason': '210f5211d9f9a3ca40a881298b00de8be3465a21941376ee44885efe952c7565'},
 'trading_runtime/numbered_session_exit.py': {'numbered_session_exit_intent': 'ef9bf3c91d18c5059f3d58ea844397a0d764b7cad9188a865a4d7260e85d0550'},
 'trading_runtime/runtime.py': {'_execute_intents': '84508aec84c4ad2ece79f428fca4ae7c0c3abbf0b9d664b2307d558c712ad021'}}


def certify_numbered_session_exit_reason_source(*, source_overrides=None) -> str:
    """Bind the shared reason map and all three real proposal/admission paths.

    Full reviewed functions retain surrounding scalar source, mode and
    provenance guards. Mutation tests must not bypass a caller or the map.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(_SESSION_EXIT_REVIEWED_AST):
        raise ValueError("Session-exit source override is outside reviewed authority")
    root = Path(__file__).parents[1]
    observed = []
    for relative, expected in _SESSION_EXIT_REVIEWED_AST.items():
        source = overrides.get(relative, root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name, digest in expected.items():
            if name == "_SESSION_EXIT_REASONS":
                nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == name
                                 for target in node.targets)]
            else:
                nodes = [node for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == name]
            if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                raise ValueError("Numbered session-exit reviewed authority changed: " + relative + ":" + name)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(",", ":")).encode()).hexdigest()
