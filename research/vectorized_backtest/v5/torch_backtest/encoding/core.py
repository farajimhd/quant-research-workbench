"""Bounded, backward-referencing expression programs; no Python row callbacks."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from numbers import Integral, Real

import polars as pl

UNUSED = 2**31 - 1


class EncodingError(ValueError):
    """An encoding, input contract or parameter constraint is invalid."""


class Unit(StrEnum):
    PRICE = "price"
    MONEY = "money"
    SHARES = "shares"
    COUNT = "count"
    SECONDS = "seconds"
    RSI = "rsi_points"
    RETURN = "return"
    BPS = "bps"
    RATIO = "ratio"
    FRACTION = "cash_fraction"
    BOOLEAN = "boolean"


class Operation(IntEnum):
    # Labels are serialized ABI: changing their meanings requires a new version.
    PAD = 0
    INPUT = 1
    PARAMETER = 2
    ADD = 3
    SUBTRACT = 4
    MULTIPLY = 5
    DIVIDE = 6
    RELATIVE_GAP = 7
    GAP_BPS = 8
    TO_BPS = 9
    GREATER = 10
    GREATER_EQUAL = 11
    LESS = 12
    LESS_EQUAL = 13
    EQUAL = 14
    AND = 15
    OR = 16
    NOT = 17
    LAG = 18
    ROLLING_MIN = 19
    ROLLING_MAX = 20
    CROSS_ABOVE = 21
    CROSS_BELOW = 22
    ENTER = 30
    EXIT = 31
    ADD_POSITION = 32
    SET_STOP = 33
    SET_TARGET = 34


@dataclass(frozen=True)
class AtomicInput:
    label: int
    name: str
    column: str
    unit: Unit
    resolution_ms: int | None = None
    valid_column: str | None = None
    available_at_column: str | None = None
    scale: float = 1.0
    source: str = "state"


@dataclass(frozen=True)
class Parameter:
    name: str
    unit: Unit
    minimum: float
    maximum: float
    integer: bool = False
    choices: tuple[float, ...] = ()


@dataclass(frozen=True)
class Constraint:
    """Cross-parameter relation, e.g. min_price < max_price."""

    left: int
    operator: str
    right: int


@dataclass(frozen=True)
class Instruction:
    operation: int
    a: int = UNUSED
    b: int = UNUSED
    parameter: int = UNUSED

    def row(self) -> tuple[int, int, int, int]:
        values = (self.operation, self.a, self.b, self.parameter)
        if any(
            not isinstance(value, Integral) or isinstance(value, bool)
            for value in values
        ):
            raise EncodingError("Instruction class labels must be integers")
        return tuple(int(value) for value in values)


@dataclass(frozen=True)
class Program:
    # instructions [L,4] int64; values [P] float64; mask [L] bool.
    instructions: tuple[tuple[int, int, int, int], ...]
    values: tuple[float, ...]
    mask: tuple[bool, ...]
    version: str = "atomic-polars-program-v1"

    @classmethod
    def encode(
        cls,
        instructions: Iterable[Instruction],
        values: Iterable[float],
        *,
        pad_to: int | None = None,
    ) -> Program:
        rows = tuple(i.row() for i in instructions)
        active = len(rows)
        length = active if pad_to is None else pad_to
        if type(length) is not int or not 1 <= active <= length <= 64:
            raise EncodingError("Require 1..64 instructions and valid padding length")
        return cls(
            rows + ((0, UNUSED, UNUSED, UNUSED),) * (length - active),
            tuple(values),
            (True,) * active + (False,) * (length - active),
        )

    @classmethod
    def from_arrays(cls, instructions, values, mask) -> Program:
        """Accept nested lists or CPU NumPy/tensor .tolist(); never round labels.

        Shapes must be [L,4], [P], [L]. GPU tensors must be explicitly transferred
        by the caller; compilation is once-per-candidate CPU orchestration.
        """
        unpack = lambda x: x.tolist() if hasattr(x, "tolist") else x
        try:
            return cls(
                tuple(tuple(row) for row in unpack(instructions)),
                tuple(unpack(values)),
                tuple(unpack(mask)),
            )
        except TypeError as error:
            raise EncodingError("Array shapes must be [L,4], [P], [L]") from error

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "instructions": self.instructions,
            "values": self.values,
            "mask": self.mask,
        }

    def to_arrays(self):
        """Export [L,4] int64, [P] float64 and [L] Boolean NumPy arrays.

        NumPy is optional until this method is called. Float operation labels
        are rejected before conversion so an optimizer cannot silently round
        an invalid categorical proposal into a different executable strategy.
        """
        import numpy as np

        if any(
            not isinstance(x, Integral) or isinstance(x, bool)
            for row in self.instructions
            for x in row
        ):
            raise EncodingError("Cannot export noninteger instruction labels")
        return (
            np.asarray(self.instructions, dtype=np.int64),
            np.asarray(self.values, dtype=np.float64),
            np.asarray(self.mask, dtype=np.bool_),
        )

    @classmethod
    def from_dict(cls, payload: dict) -> Program:
        if set(payload) != {"version", "instructions", "values", "mask"}:
            raise EncodingError("Unexpected serialized program fields")
        p = cls.from_arrays(payload["instructions"], payload["values"], payload["mask"])
        return cls(p.instructions, p.values, p.mask, payload["version"])


@dataclass(frozen=True)
class Catalog:
    inputs: tuple[AtomicInput, ...]
    parameters: tuple[Parameter, ...]
    constraints: tuple[Constraint, ...] = ()
    group_columns: tuple[str, ...] = ("listing_id", "session_date")
    clock_column: str = "time_us"
    max_lookback: int = 4096


@dataclass(frozen=True)
class CompiledStrategy:
    expressions: tuple[pl.Expr, ...]
    stages: tuple[pl.Expr, ...]
    dependencies: tuple[AtomicInput, ...]
    required_columns: tuple[str, ...]
    group_columns: tuple[str, ...]
    clock_column: str
    fingerprint: str
    has_temporal: bool
    history_rows: int

    def evaluate(self, frame: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
        """Validate the as-of observation grid, then execute native Polars.

        Rows are decision-clock observations, not raw multi-resolution candles.
        The upstream adapter must supply latest *completed* values per listing.
        Availability clocks are UTC integer microseconds. Null/invalid/future
        inputs propagate as unknown; action predicates fail closed.
        """
        data = frame.collect() if isinstance(frame, pl.LazyFrame) else frame
        missing = set(self.required_columns) - set(data.columns)
        if missing:
            raise EncodingError(f"Missing input columns: {sorted(missing)}")
        keys = [*self.group_columns, self.clock_column]
        if not data.schema[self.clock_column].is_integer():
            raise EncodingError("Decision clock must be integer UTC microseconds")
        for feature in self.dependencies:
            if (
                feature.available_at_column
                and not data.schema[feature.available_at_column].is_integer()
            ):
                raise EncodingError(
                    "Availability clocks must be integer UTC microseconds"
                )
        if data.select(pl.any_horizontal(pl.col(keys).is_null()).any()).item():
            raise EncodingError("Null identity/session/clock key")
        if data.select(pl.struct(keys).n_unique()).item() != data.height:
            raise EncodingError("Duplicate decision-clock identity")
        # Temporal windows are chronological observation windows, partitioned by
        # identity AND session. Never allow row ordering or overnight leakage.
        plan = data.sort(keys).lazy()
        for stage in self.stages:
            # Materialized aliases avoid nested Polars window expressions and
            # exponential duplication of a graph's shared subexpressions.
            plan = plan.with_columns(stage)
        return plan.select(*keys, *self.expressions).collect()


def compile_strategy(program: Program, catalog: Catalog) -> CompiledStrategy:
    """Type-check once; produce vectorized expressions and exact dependencies."""

    def reject(message):
        raise EncodingError(message)

    if program.version != "atomic-polars-program-v1":
        reject("Unsupported program version")
    if not 1 <= len(program.instructions) <= 64 or len(program.mask) != len(
        program.instructions
    ):
        reject("Instruction/mask shapes must be [L,4] and [L], 1<=L<=64")
    if len(program.values) != len(catalog.parameters) or len(program.values) > 64:
        reject("Parameter shape must match catalog [P], P<=64")
    if not catalog.group_columns or len(set(catalog.group_columns)) != len(
        catalog.group_columns
    ):
        reject("Temporal identity/session partition is required")
    if (
        catalog.clock_column in catalog.group_columns
        or type(catalog.max_lookback) is not int
        or catalog.max_lookback < 1
    ):
        reject("Invalid clock/lookback catalog")
    by_label = {}
    for feature in catalog.inputs:
        if (
            type(feature.label) is not int
            or not 0 <= feature.label < UNUSED
            or feature.label in by_label
            or not isinstance(feature.unit, Unit)
            or not feature.name
            or not feature.column
            or not math.isfinite(feature.scale)
            or feature.scale <= 0
        ):
            reject("Invalid/duplicate atomic input label or metadata")
        if feature.source != "state" and not feature.available_at_column:
            reject("Market inputs require explicit availability evidence")
        if feature.resolution_ms is not None and (
            type(feature.resolution_ms) is not int or feature.resolution_ms <= 0
        ):
            reject("Invalid feature resolution")
        by_label[feature.label] = feature
    for spec, value in zip(catalog.parameters, program.values):
        if (
            not isinstance(spec.unit, Unit)
            or spec.unit == Unit.BOOLEAN
            or not math.isfinite(spec.minimum)
            or not math.isfinite(spec.maximum)
            or spec.minimum > spec.maximum
            or not isinstance(value, Real)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not spec.minimum <= value <= spec.maximum
            or spec.integer
            and value != int(value)
            or spec.choices
            and value not in spec.choices
        ):
            reject(f"Invalid parameter range/value: {spec.name}")
        if spec.unit == Unit.RSI and not 0 <= spec.minimum <= spec.maximum <= 100:
            reject("RSI parameter ranges must lie within 0..100")
        if spec.unit == Unit.FRACTION and not 0 <= spec.minimum <= spec.maximum <= 1:
            reject("Cash fraction ranges must lie within 0..1")
    relations = {
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b,
    }
    for relation in catalog.constraints:
        if (
            type(relation.left) is not int
            or type(relation.right) is not int
            or not 0 <= relation.left < len(program.values)
            or not 0 <= relation.right < len(program.values)
            or relation.operator not in relations
        ):
            reject("Invalid cross-parameter constraint")
        if (
            catalog.parameters[relation.left].unit
            != catalog.parameters[relation.right].unit
        ):
            reject("Cross-parameter constraint has incompatible units")
        if not relations[relation.operator](
            program.values[relation.left], program.values[relation.right]
        ):
            reject("Cross-parameter constraint violated")

    results = []
    dependencies = {}
    outputs = []
    padded = False
    temporal = False
    stages = []
    histories = []

    def finite(expr):
        return pl.when(expr.is_finite()).then(expr).otherwise(None)

    def operand(label, index):
        if label == UNUSED:
            reject("Required operand is absent")
        if label < 0:
            position = -label - 1
            if position >= index or results[position] is None:
                reject("Result references must target earlier non-action instructions")
            return results[position]
        if label not in by_label:
            reject(f"Unknown atomic input label: {label}")
        feature = by_label[label]
        dependencies[label] = feature
        value = pl.col(feature.column)
        if feature.unit == Unit.BOOLEAN:
            value = value.cast(pl.Boolean, strict=True)
        else:
            value = finite(value.cast(pl.Float64, strict=True) * feature.scale)
        valid = pl.lit(True)
        if feature.valid_column:
            valid &= (
                pl.col(feature.valid_column)
                .cast(pl.Boolean, strict=True)
                .fill_null(False)
            )
        if feature.available_at_column:
            valid &= (
                pl.col(feature.available_at_column) <= pl.col(catalog.clock_column)
            ).fill_null(False)
        return pl.when(valid).then(value).otherwise(None), feature.unit

    def lag(expr):
        return expr.shift(1).over(
            list(catalog.group_columns), order_by=catalog.clock_column
        )

    unary = {
        Operation.INPUT,
        Operation.TO_BPS,
        Operation.NOT,
        Operation.LAG,
        Operation.ROLLING_MIN,
        Operation.ROLLING_MAX,
        Operation.EXIT,
    }
    binary = set(Operation) - unary - {Operation.PARAMETER, Operation.PAD}
    windows = {Operation.LAG, Operation.ROLLING_MIN, Operation.ROLLING_MAX}
    for index, (row, active) in enumerate(zip(program.instructions, program.mask)):
        if (
            len(row) != 4
            or any(not isinstance(x, Integral) or isinstance(x, bool) for x in row)
            or type(active) is not bool
        ):
            reject("Instruction labels must be integers; masks must be Boolean")
        try:
            op = Operation(row[0])
        except ValueError:
            reject(f"Unknown operation label: {row[0]}")
        _, a, b, p = row
        if not active:
            if tuple(row) != (0, UNUSED, UNUSED, UNUSED):
                reject("Masked rows must be canonical PAD")
            padded = True
            results.append(None)
            histories.append(0)
            continue
        if padded or op == Operation.PAD:
            reject("Active instructions must be a non-PAD prefix")
        if op not in windows | {Operation.PARAMETER} and p != UNUSED:
            reject("Unexpected parameter slot")
        if op not in binary and b != UNUSED:
            reject("Unexpected second operand")
        if op == Operation.PARAMETER:
            if a != UNUSED or not 0 <= p < len(program.values):
                reject("Invalid parameter reference")
            expression = pl.lit(float(program.values[p]))
            alias = f"__strategy_node_{index}"
            stages.append(expression.alias(alias))
            results.append((pl.col(alias), catalog.parameters[p].unit))
            histories.append(1)
            continue
        history = max(
            (histories[-label - 1] if label < 0 and -label - 1 < index else 1)
            for label in (a, b)
            if label != UNUSED
        )
        x, ux = operand(a, index)
        if op == Operation.INPUT:
            if a < 0:
                reject("INPUT requires an atomic input label")
            expression, unit = x, ux
        elif op in windows:
            if not 0 <= p < len(program.values):
                reject("Missing lookback parameter")
            spec = catalog.parameters[p]
            window = program.values[p]
            if (
                not spec.integer
                or spec.unit != Unit.COUNT
                or not 1 <= window <= catalog.max_lookback
            ):
                reject("Lookback must be bounded positive integer observations")
            if ux == Unit.BOOLEAN and op != Operation.LAG:
                reject("Rolling extrema require numeric input")
            window = int(window)
            temporal = True
            history += window if op == Operation.LAG else window - 1
            expression = (
                x.shift(window)
                if op == Operation.LAG
                else x.rolling_min(window_size=window, min_samples=window)
                if op == Operation.ROLLING_MIN
                else x.rolling_max(window_size=window, min_samples=window)
            )
            expression = expression.over(
                list(catalog.group_columns), order_by=catalog.clock_column
            )
            unit = ux
        elif op == Operation.TO_BPS:
            if ux != Unit.RETURN:
                reject("TO_BPS requires return units")
            expression, unit = finite(x * 10000), Unit.BPS
        elif op == Operation.NOT:
            if ux != Unit.BOOLEAN:
                reject("NOT requires Boolean input")
            expression, unit = ~x, ux
        elif op == Operation.EXIT:
            if ux != Unit.BOOLEAN:
                reject("EXIT requires Boolean predicate")
            outputs.append(x.fill_null(False).alias(f"action_{index}_exit"))
            results.append(None)
            histories.append(history)
            continue
        else:
            y, uy = operand(b, index)
            if op in {Operation.AND, Operation.OR}:
                if ux != Unit.BOOLEAN or uy != Unit.BOOLEAN:
                    reject("Boolean operands required")
                expression, unit = (
                    (x & y if op == Operation.AND else x | y),
                    Unit.BOOLEAN,
                )
            elif op in {
                Operation.GREATER,
                Operation.GREATER_EQUAL,
                Operation.LESS,
                Operation.LESS_EQUAL,
                Operation.EQUAL,
                Operation.CROSS_ABOVE,
                Operation.CROSS_BELOW,
            }:
                if ux != uy:
                    reject("Comparison requires equal units")
                if ux == Unit.BOOLEAN and op != Operation.EQUAL:
                    reject("Ordered comparisons require numeric inputs")
                if op == Operation.CROSS_ABOVE:
                    expression = (x > y) & (lag(x) <= lag(y))
                    temporal = True
                    history += 1
                elif op == Operation.CROSS_BELOW:
                    expression = (x < y) & (lag(x) >= lag(y))
                    temporal = True
                    history += 1
                else:
                    expression = {
                        Operation.GREATER: x > y,
                        Operation.GREATER_EQUAL: x >= y,
                        Operation.LESS: x < y,
                        Operation.LESS_EQUAL: x <= y,
                        Operation.EQUAL: x == y,
                    }[op]
                unit = Unit.BOOLEAN
            elif op in {
                Operation.ENTER,
                Operation.ADD_POSITION,
                Operation.SET_STOP,
                Operation.SET_TARGET,
            }:
                expected = (
                    Unit.FRACTION
                    if op in {Operation.ENTER, Operation.ADD_POSITION}
                    else Unit.PRICE
                )
                if ux != Unit.BOOLEAN or uy != expected:
                    reject("Action predicate/value unit mismatch")
                allowed = y.is_between(0, 1) if expected == Unit.FRACTION else y > 0
                gate = (x & allowed).fill_null(False)
                name = f"action_{index}_{op.name.lower()}"
                outputs.extend(
                    (
                        gate.alias(name),
                        pl.when(gate).then(y).otherwise(None).alias(name + "_value"),
                    )
                )
                results.append(None)
                histories.append(history)
                continue
            else:
                if Unit.BOOLEAN in {ux, uy}:
                    reject("Arithmetic requires numeric operands")
                if op in {Operation.ADD, Operation.SUBTRACT}:
                    if ux != uy:
                        reject("Add/subtract require equal units")
                    expression = x + y if op == Operation.ADD else x - y
                    unit = ux
                elif op == Operation.MULTIPLY:
                    if uy == Unit.RATIO:
                        unit = ux
                    elif ux == Unit.RATIO:
                        unit = uy
                    else:
                        reject("Multiply requires a dimensionless ratio operand")
                    expression = x * y
                else:
                    if op in {Operation.RELATIVE_GAP, Operation.GAP_BPS}:
                        if ux != uy:
                            reject("Relative gap requires equal units")
                        expression = x / y - 1
                        unit = Unit.RETURN if op == Operation.RELATIVE_GAP else Unit.BPS
                        if op == Operation.GAP_BPS:
                            expression *= 10000
                    elif op == Operation.DIVIDE:
                        if ux == uy:
                            unit = Unit.RATIO
                        elif uy == Unit.RATIO:
                            unit = ux
                        else:
                            reject("Unsupported quotient dimensions")
                        expression = x / y
                    else:
                        reject("Operation is not implemented")
                    expression = pl.when(y != 0).then(expression).otherwise(None)
                expression = finite(expression)
        alias = f"__strategy_node_{index}"
        stages.append(expression.alias(alias))
        results.append((pl.col(alias), unit))
        histories.append(history)
    if not outputs:
        reject("Program must produce at least one action proposal")
    if max(histories) > catalog.max_lookback:
        reject("Composed temporal history exceeds the catalog bound")
    output_names = [expr.meta.output_name() for expr in outputs]
    for kind in ("enter", "exit", "add_position", "set_stop", "set_target"):
        if sum(name.endswith("_" + kind) for name in output_names) > 1:
            reject("Combine predicates before emitting one proposal per action kind")
    dependencies = tuple(dependencies[k] for k in sorted(dependencies))
    required = set(catalog.group_columns) | {catalog.clock_column}
    for feature in dependencies:
        required.add(feature.column)
        required.update(
            x for x in (feature.valid_column, feature.available_at_column) if x
        )
    from dataclasses import asdict

    payload = {"program": program.to_dict(), "catalog": asdict(catalog)}
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CompiledStrategy(
        tuple(outputs),
        tuple(stages),
        dependencies,
        tuple(sorted(required)),
        catalog.group_columns,
        catalog.clock_column,
        fingerprint,
        temporal,
        max(histories),
    )
