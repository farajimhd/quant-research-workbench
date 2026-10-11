"""Shared typed rules over producer-owned, completed columnar inputs.

No market acquisition, history calculation, broker state, or persistence is
performed here. Temporal windows count observed rows within the producer's
explicit evaluation block (including its causal warm-up), not future rows.
"""
from dataclasses import dataclass
from enum import IntEnum
from math import isfinite
import re
from typing import Mapping

import polars as pl


class Operation(IntEnum):
    FEATURE = 0
    CONSTANT = 1
    ADD = 2
    SUBTRACT = 3
    MULTIPLY = 4
    DIVIDE = 5
    GREATER = 6
    GREATER_EQUAL = 7
    LESS = 8
    LESS_EQUAL = 9
    AND = 10
    OR = 11
    NOT = 12
    LAG = 13
    MEAN = 14
    MINIMUM = 15
    MAXIMUM = 16
    DIFFERENCE = 17
    CROSS_ABOVE = 18
    CROSS_BELOW = 19
    ABS = 20


UNARY = {Operation.NOT, Operation.LAG, Operation.MEAN, Operation.MINIMUM,
         Operation.MAXIMUM, Operation.DIFFERENCE, Operation.ABS}
TEMPORAL = {Operation.LAG, Operation.MEAN, Operation.MINIMUM,
            Operation.MAXIMUM, Operation.DIFFERENCE}
WINDOWS = {1, 2, 3, 5, 10, 20, 30, 60, 120}
GROUP = ["listing_id", "evaluation_block"]


@dataclass(frozen=True)
class InputField:
    name: str
    unit: str
    product: str
    revision: str
    resolution_ms: int

    def __post_init__(self):
        if (re.fullmatch(r"[a-z][a-z0-9_.]*", self.name or "") is None or not self.unit
                or re.fullmatch(r"arte\.[a-z][a-z0-9_]*", self.product or "") is None
                or re.fullmatch(r"[0-9a-f]{64}", self.revision or "") is None
                or type(self.resolution_ms) is not int
                or self.resolution_ms < 100 or self.resolution_ms % 100):
            raise ValueError("Rule field needs an exact producer-owned input contract")
        # Position-open/age are applied by the sequential coordinator, never
        # represented as a vectorized market field or filled execution price.
        if not self.name.startswith(("history.", "market.", "bar.", "indicator.", "level.")):
            raise ValueError("Programs cannot access execution-derived position fields")
        if (self.product.startswith("arte.trading_") or any(token in self.name.lower()
                for token in ("entry_price", "average_cost", "unrealized", "realized",
                              "pnl", "portfolio", "position", "fill_price"))):
            raise ValueError("Programs cannot access execution-derived position fields")


@dataclass(frozen=True)
class RuleNode:
    operation: Operation
    left: int = -1
    right: int = -1
    field: str = ""
    window: int = 1
    literal: float = 0.0
    unit: str = "ratio"


@dataclass(frozen=True)
class RuleProgram:
    nodes: tuple[RuleNode, ...]
    output: int

    def validate(self, fields: Mapping[str, InputField]) -> None:
        if (not 1 <= len(self.nodes) <= 32 or type(self.output) is not int
                or not 0 <= self.output < len(self.nodes)):
            raise ValueError("Rule program exceeds bounded DAG contract")
        units, context = [], []
        for index, node in enumerate(self.nodes):
            if (type(node.operation) not in (int, Operation)
                    or type(node.left) is not int or type(node.right) is not int):
                raise ValueError("Rule instructions require typed integer operands")
            op = Operation(node.operation)
            if (type(node.window) is not int or node.window not in WINDOWS
                    or not isfinite(node.literal)):
                raise ValueError("Invalid rule window or literal")
            depth = 0
            if op == Operation.FEATURE:
                if node.field not in fields:
                    raise ValueError(f"Uncertified program field: {node.field}")
                unit = fields[node.field].unit
            elif op == Operation.CONSTANT:
                unit = node.unit
                if unit not in {f.unit for f in fields.values()} | {"bool", "ratio"}:
                    raise ValueError("Unknown rule literal unit")
                if unit == "bool" and node.literal not in (0, 1):
                    raise ValueError("Boolean rule literal must be 0 or 1")
            else:
                if (type(node.left) is not int or not 0 <= node.left < index
                        or (op not in UNARY and (type(node.right) is not int
                                                or not 0 <= node.right < index))):
                    raise ValueError("Rule operands must precede their consumer")
                a = units[node.left]
                b = a if op in UNARY else units[node.right]
                depth = max(context[node.left], context[node.left] if op in UNARY
                            else context[node.right])
                if op in {Operation.AND, Operation.OR, Operation.NOT}:
                    if a != "bool" or b != "bool":
                        raise ValueError("Boolean rule operands required")
                    unit = "bool"
                elif op in {Operation.GREATER, Operation.GREATER_EQUAL, Operation.LESS,
                            Operation.LESS_EQUAL, Operation.CROSS_ABOVE, Operation.CROSS_BELOW}:
                    if a != b:
                        raise ValueError("Comparison rule units differ")
                    unit = "bool"
                    depth += int(op in {Operation.CROSS_ABOVE, Operation.CROSS_BELOW})
                elif op in {Operation.ADD, Operation.SUBTRACT}:
                    if a != b or a == "bool":
                        raise ValueError("Arithmetic rule units differ")
                    unit = a
                elif op == Operation.MULTIPLY:
                    if "bool" in (a, b) or "ratio" not in (a, b):
                        raise ValueError("Multiplication needs a ratio operand")
                    unit = b if a == "ratio" else a
                elif op == Operation.DIVIDE:
                    if "bool" in (a, b) or (a != b and b != "ratio"):
                        raise ValueError("Division rule units differ")
                    unit = "ratio" if a == b else a
                else:
                    if a == "bool" and op in {Operation.MEAN, Operation.DIFFERENCE, Operation.ABS}:
                        raise ValueError("Numeric rule operand required")
                    unit = a
                    depth += (node.window if op in {Operation.LAG, Operation.DIFFERENCE}
                              else node.window - 1 if op in TEMPORAL else 0)
                if depth > 119:
                    raise ValueError("Rule exceeds 119-row causal context")
            units.append(unit)
            context.append(depth)
        if units[self.output] != "bool":
            raise ValueError("Strategy rule output must be Boolean")


def evaluate_rules(frame: pl.DataFrame, programs: Mapping[str, RuleProgram],
                   fields: Mapping[str, InputField]) -> pl.DataFrame:
    """Bounded instruction loop; all listing/clock arithmetic is Polars-native.

    Each input value has an explicit `<field>__valid` Boolean column. Missing
    inputs stay invalid through NOT and OR, matching search mask propagation.
    The caller supplies warm-up and trims it only after rule evaluation.
    """
    if frame.height > 262_144:
        raise ValueError("Rule frame exceeds bounded evaluation envelope")
    required = {*GROUP, "observed_ordinal", "available_at", "boundary_at"}
    if not required <= set(frame.columns):
        raise ValueError("Rule frame lacks producer causal clocks and block identity")
    for key in ("available_at", "boundary_at"):
        dtype = frame.schema[key]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
            raise ValueError("Rule clocks must be timezone-aware")
    if (frame.select(pl.any_horizontal(pl.col(c).is_null() for c in required).any()).item()
            or frame.select((pl.col("available_at") > pl.col("boundary_at")).any()).item()
            or frame.select(pl.struct(*GROUP, "observed_ordinal").is_duplicated().any()).item()
            or frame.select((pl.col("observed_ordinal").diff().over(GROUP).drop_nulls() != 1).any()).item()
            or frame.select((pl.col("boundary_at").diff().over(GROUP).drop_nulls()
                             <= pl.duration(microseconds=0)).any()).item()
            or not frame.select(*GROUP, "observed_ordinal").equals(
                frame.select(*GROUP, "observed_ordinal").sort(*GROUP, "observed_ordinal"))):
        raise ValueError("Rule frame has future, duplicate, missing or unordered rows")
    outputs = frame.select(*GROUP, "observed_ordinal", "boundary_at")
    for name, program in programs.items():
        if not name.isidentifier():
            raise ValueError("Rule output name must be an identifier")
        program.validate(fields)
        work = frame
        for index, node in enumerate(program.nodes):
            op = Operation(node.operation)
            value_name, mask_name = f"__rule_value_{index}", f"__rule_valid_{index}"
            a = pl.col(f"__rule_value_{node.left}")
            av = pl.col(f"__rule_valid_{node.left}")
            b = pl.col(f"__rule_value_{node.right}")
            bv = pl.col(f"__rule_valid_{node.right}")
            if op == Operation.FEATURE:
                if (node.field not in frame.columns or node.field + "__valid" not in frame.columns
                        or frame.schema[node.field + "__valid"] != pl.Boolean):
                    raise ValueError(f"Missing typed input/mask: {node.field}")
                value = pl.col(node.field).cast(pl.Float32)
                mask = pl.col(node.field + "__valid").fill_null(False)
            elif op == Operation.CONSTANT:
                value, mask = pl.lit(node.literal, dtype=pl.Float32), pl.lit(True)
            elif op in TEMPORAL:
                k = node.window
                prior = a.shift(k).over(GROUP)
                prior_valid = av.shift(k).over(GROUP).fill_null(False)
                if op == Operation.LAG:
                    value, mask = prior, prior_valid
                elif op == Operation.DIFFERENCE:
                    value, mask = a - prior, av & prior_valid
                else:
                    safe = pl.when(av).then(a).otherwise(pl.lit(0, dtype=pl.Float32))
                    count = av.cast(pl.UInt32).rolling_sum(k, min_samples=k).over(GROUP)
                    mask = (count == k).fill_null(False)
                    if op == Operation.MEAN:
                        # Preserve the optimizer's Float32 cumulative subtraction
                        # and explicit block reset, rather than rolling_mean's
                        # different numeric accumulation contract.
                        work = work.with_columns(safe.cum_sum().over(GROUP).alias("__rule_sum"))
                        sums = pl.col("__rule_sum")
                        value = a if k == 1 else (
                            sums - sums.shift(k).over(GROUP).fill_null(0)) / pl.lit(k, dtype=pl.Float32)
                    elif op == Operation.MINIMUM:
                        value = safe.rolling_min(k, min_samples=k).over(GROUP)
                    else:
                        value = safe.rolling_max(k, min_samples=k).over(GROUP)
            else:
                mask = av if op in UNARY else av & bv
                if op == Operation.ADD: value = a + b
                elif op == Operation.SUBTRACT: value = a - b
                elif op == Operation.MULTIPLY: value = a * b
                elif op == Operation.DIVIDE:
                    mask = mask & (b.abs() > 1e-12)
                    value = a / pl.when(b.abs() > 1e-12).then(b).otherwise(1)
                elif op == Operation.GREATER: value = a > b
                elif op == Operation.GREATER_EQUAL: value = a >= b
                elif op == Operation.LESS: value = a < b
                elif op == Operation.LESS_EQUAL: value = a <= b
                elif op == Operation.AND: value = (a != 0) & (b != 0)
                elif op == Operation.OR: value = (a != 0) | (b != 0)
                elif op == Operation.NOT: value = a == 0
                elif op == Operation.ABS: value = a.abs()
                else:
                    mask = mask & (av & bv).shift(1).over(GROUP).fill_null(False)
                    prior_a, prior_b = a.shift(1).over(GROUP), b.shift(1).over(GROUP)
                    value = ((a > b) & (prior_a <= prior_b) if op == Operation.CROSS_ABOVE
                             else (a < b) & (prior_a >= prior_b))
            numeric = value.cast(pl.Float32)
            mask = (mask & numeric.is_finite()).fill_null(False)
            work = work.with_columns(mask.alias(mask_name),
                pl.when(mask).then(numeric).otherwise(pl.lit(0, dtype=pl.Float32)).alias(value_name))
        outputs = outputs.with_columns(
            ((work[f"__rule_value_{program.output}"] != 0)
             & work[f"__rule_valid_{program.output}"]).alias(name),
            work[f"__rule_valid_{program.output}"].alias(name + "__valid"))
    return outputs
