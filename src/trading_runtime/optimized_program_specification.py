"""Translate the frozen winner into shared typed rule specifications.

This adapter is not an executor or market source. No strategy tables, assumed
fills, research feature-bank reads, publication or run admission are provided.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from src.market_engine.volume_rank_rules import VolumeUniverseRule
from .columnar_rule_program import InputField, Operation, RuleNode, RuleProgram
from .optimized_strategy_reference import OptimizationReference, WINNER_SHA256, GENERATION


STAGES = ("entry", "exit", "add", "reduce", "trail")
# These are stable source catalog identities, verified against the frozen
# winner's installed v7-original-plus-causal-history-relative-v1 catalog.
WINNER_FIELDS = MappingProxyType({
    326: ("history.attention_mean.58", "dollars"),
    512: ("history.recent_high.32.relative_to_close", "ratio"),
    538: ("history.recent_high.58.relative_to_close", "ratio"),
    561: ("history.momentum_close.21.relative_to_close", "ratio"),
    570: ("history.momentum_close.30.relative_to_close", "ratio"),
    677: ("history.swing_low.5.2.relative_to_close", "ratio"),
    687: ("history.swing_low.10.1.relative_to_close", "ratio"),
    698: ("history.swing_low.15.1.relative_to_close", "ratio"),
})
STOP_FIELD = "history.swing_low.2.1"
WINNER_SEMANTIC_HASH = "8f43c8692d9441491e4275eda117b4724a26c24c90487639c5505b9f81b1c319"


@dataclass(frozen=True)
class OptimizedProgramSpecification:
    winner_hash: str
    generation: int
    candidate: int
    programs: Mapping[str, RuleProgram]
    minimum_age_seconds: Mapping[str, int]
    policy: Mapping[str, float | int]
    entry_cash: float
    add_cash: float
    commission_per_share: float
    minimum_commission: float
    universe: VolumeUniverseRule
    semantic_hash: str


def compile_frozen_winner(reference: OptimizationReference,
                          fields: Mapping[str, InputField], *,
                          universe_identity: Mapping) -> OptimizedProgramSpecification:
    """Require explicit certified fields and the actual source universe contract.

    No pricing/window defaults are inferred. The stored market producer used
    split-adjusted prior-session close for eligibility before volume ranking.
    """
    if reference.winner_sha256 != WINNER_SHA256 or reference.generation != GENERATION:
        raise ValueError("Winner differs from the existing frozen comparison reference")
    semantic = json.dumps(reference.program, sort_keys=True, separators=(",", ":"))
    if sha256(semantic.encode()).hexdigest() != WINNER_SEMANTIC_HASH:
        raise ValueError("Frozen winner program content changed")
    if universe_identity.get("version") != "v6-volume-market-blocks-v1":
        raise ValueError("Unknown optimization universe semantics")
    universe = VolumeUniverseRule(universe_identity["window"], universe_identity["top_n"],
                                  universe_identity["minimum_close"], universe_identity["maximum_close"])
    if universe != VolumeUniverseRule(30, 10, .8, 50):
        raise ValueError("Frozen optimization volume top10 universe changed")
    required = {name for name, unit in WINNER_FIELDS.values()} | {STOP_FIELD}
    if not required <= set(fields):
        raise ValueError("Winner lacks certified producer history inputs")
    for name, unit in WINNER_FIELDS.values():
        if fields[name].unit != unit or fields[name].resolution_ms != 1000:
            raise ValueError("Winner input unit or resolution differs from search")
    if fields[STOP_FIELD].unit != "price" or fields[STOP_FIELD].resolution_ms != 1000:
        raise ValueError("Winner swing stop needs its certified price history")
    programs = {}
    for stage in STAGES:
        raw = reference.program["rules"][stage] if stage == "entry" else (
            reference.program.get("open_rules") or reference.program["rules"])[stage]
        nodes = []
        for n in raw["nodes"]:
            op = Operation(n["op"])
            name = WINNER_FIELDS[n["feature"]][0] if op == Operation.FEATURE else ""
            nodes.append(RuleNode(op, n["a"], n["b"], name, n["window"], n["value"], n["unit"]))
        program = RuleProgram(tuple(nodes), raw["output"])
        program.validate(fields)
        programs[stage] = program
    ages = dict(reference.program.get("minimum_age") or {})
    if any(stage not in STAGES or type(age) is not int or age < 0 for stage, age in ages.items()):
        raise ValueError("Invalid winner position age restriction")
    execution = reference.execution
    numbers = [execution[key] for key in ("entry_dollars", "add_dollars", "fee_per_share", "minimum_order_fee")]
    if any(type(v) not in (float, int) or not isfinite(v) or v < 0 for v in numbers):
        raise ValueError("Invalid frozen sizing or commission")
    if (numbers != [1000., 200., .005, 1.]
            or type(execution.get("cost_bps")) not in (int, float)
            or execution["cost_bps"] != 0):
        raise ValueError("Frozen comparison execution contract changed")
    from .columnar_strategy_release import POLICY
    policy = dict(reference.program["policy"])
    expected = (set(POLICY) - {"cooldown_seconds"}) | {"cooldown"}
    if set(policy) != expected:
        raise ValueError("Frozen policy differs from the optimization contract")
    # V7's completed clock is in elapsed seconds, as is native management.
    # Preserve the value exactly; only the shared field name changes.
    policy["cooldown_seconds"] = policy.pop("cooldown")
    raw = json.dumps({"program": reference.program, "execution": dict(execution),
                      "universe": dict(universe_identity)}, sort_keys=True, separators=(",", ":"))
    return OptimizedProgramSpecification(reference.winner_sha256, reference.generation,
        reference.candidate, MappingProxyType(programs), MappingProxyType(ages),
        MappingProxyType(policy), *map(float, numbers),
        universe, sha256(raw.encode()).hexdigest())
