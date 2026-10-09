"""Searchable scalar expression graphs compiled once before causal replay.

ABI: instructions [L,4] int64 = (opcode, a, b, parameter_index). Nonnegative
operands identify ONE atomic input; negative operands refer to earlier nodes.
Values [B,P] hold continuous/discrete thresholds for independent candidates.
The graph topology and categorical input/op labels are validated on the CPU.
Execution is only tensor math over [B,N]; no values return to Python per tick.

This version has its own namespace, rather than changing the existing Polars
ABI. The tracer exposes arithmetic and comparisons inside a reference policy;
it does not serialize an opaque policy function as an operation label.
"""

import math
from dataclasses import dataclass, replace
from enum import IntEnum

import torch

from research.vectorized_backtest.v6.torch_backtest.encoding.core import EncodingError

UNUSED = 2**31 - 1


class Op(IntEnum):
    VALUE = 1
    ADD = 2
    SUBTRACT = 3
    MULTIPLY = 4
    DIVIDE = 5
    REMAINDER = 6
    GREATER = 7
    GREATER_EQUAL = 8
    LESS = 9
    LESS_EQUAL = 10
    EQUAL = 11
    AND = 12
    OR = 13
    NOT = 14
    FINITE = 15
    ABS = 16
    ROUND = 17
    TO_BPS = 18
    FROM_BPS = 19


@dataclass(frozen=True)
class Input:
    label: int
    name: str
    kind: str  # bool / integer / real; IDs and clocks remain int64.
    unit: str = "unspecified"


@dataclass(frozen=True)
class Threshold:
    name: str
    minimum: float
    maximum: float
    integer: bool = False
    unit: str = "literal"


@dataclass(frozen=True)
class Graph:
    inputs: tuple[Input, ...]
    instructions: tuple[tuple[int, int, int, int], ...]
    values: tuple[float, ...]
    thresholds: tuple[Threshold, ...]
    output: int
    version: str = "atomic-torch-scalar-v1"
    gate: bool = True
    output_unit: str | None = None

    def validate(self):
        if (
            self.version != "atomic-torch-scalar-v1"
            or not 1 <= len(self.instructions) <= 512
        ):
            raise EncodingError("Unknown graph ABI or instruction envelope")
        if tuple(i.label for i in self.inputs) != tuple(range(len(self.inputs))):
            raise EncodingError("Input labels must be contiguous and unique")
        if len({i.name for i in self.inputs}) != len(self.inputs):
            raise EncodingError("Input names must be unique")
        if any(i.kind not in {"bool", "integer", "real"} for i in self.inputs):
            raise EncodingError("Unknown atomic input kind")
        if len(self.values) != len(self.thresholds):
            raise EncodingError("Threshold values and constraints differ")
        kinds, units = [], []

        def unit(reference):
            return (
                self.inputs[reference].unit if reference >= 0 else units[-reference - 1]
            )

        def compatible(a, b):
            return (
                a == b
                or {a, b} <= {"ratio", "return"}
                or a in {"literal", "unspecified"}
                or b in {"literal", "unspecified"}
            )

        def kind(reference, node):
            if type(reference) is not int:
                raise EncodingError("Operand labels must be integers")
            if reference >= 0:
                if reference >= len(self.inputs):
                    raise EncodingError("Unknown atomic input")
                return self.inputs[reference].kind
            index = -reference - 1
            if index >= node:
                raise EncodingError("Graph must be acyclic and backward referencing")
            return kinds[index]

        unary = {Op.NOT, Op.FINITE, Op.ABS, Op.ROUND, Op.TO_BPS, Op.FROM_BPS}
        comparisons = {Op.GREATER, Op.GREATER_EQUAL, Op.LESS, Op.LESS_EQUAL, Op.EQUAL}
        for index, row in enumerate(self.instructions):
            if len(row) != 4 or any(type(x) is not int for x in row):
                raise EncodingError("Instructions must be integer rows of length four")
            operation, a, b, parameter = row
            try:
                operation = Op(operation)
            except ValueError as exc:
                raise EncodingError("Unknown operation label") from exc
            if operation == Op.VALUE:
                if a != UNUSED or b != UNUSED or not 0 <= parameter < len(self.values):
                    raise EncodingError(
                        "Value node must reference a declared parameter"
                    )
                kinds.append(
                    "integer" if self.thresholds[parameter].integer else "real"
                )
                units.append(self.thresholds[parameter].unit)
                continue
            if parameter != UNUSED or (operation in unary and b != UNUSED):
                raise EncodingError("Unexpected operand or parameter")
            ka = kind(a, index)
            kb = None if operation in unary else kind(b, index)
            if operation in {Op.AND, Op.OR, Op.NOT}:
                if ka != "bool" or (kb is not None and kb != "bool"):
                    raise EncodingError("Boolean operation requires Boolean operands")
                result = "bool"
            elif operation == Op.FINITE:
                result = "bool"
            elif operation in comparisons:
                if (ka == "bool") != (kb == "bool"):
                    raise EncodingError("Cannot compare Boolean and numeric operands")
                result = "bool"
            else:
                if ka == "bool" or kb == "bool":
                    raise EncodingError("Arithmetic requires numeric operands")
                result = (
                    "real"
                    if operation in {Op.DIVIDE, Op.TO_BPS, Op.FROM_BPS}
                    or "real" in (ka, kb)
                    else "integer"
                )
            kinds.append(result)
            ua, ub = unit(a), unit(b) if b != UNUSED else None
            if operation in comparisons | {
                Op.ADD,
                Op.SUBTRACT,
                Op.REMAINDER,
            } and not compatible(ua, ub):
                raise EncodingError(f"Incompatible operand units: {ua}/{ub}")
            if result == "bool":
                units.append("boolean")
            elif operation == Op.TO_BPS:
                if ua not in {"ratio", "return"}:
                    raise EncodingError(
                        "Basis-point conversion requires a normalized ratio/return"
                    )
                units.append("bps")
            elif operation == Op.FROM_BPS:
                if ua != "bps":
                    raise EncodingError("Normalized conversion requires basis points")
                units.append("ratio")
            elif operation == Op.MULTIPLY and ua in {"ratio", "return"}:
                units.append(
                    "ratio"
                    if ub in {"ratio", "return"}
                    else ua
                    if ub == "literal"
                    else ub
                )
            elif operation in {Op.MULTIPLY, Op.DIVIDE} and ub in {"ratio", "return"}:
                units.append("ratio" if ua in {"ratio", "return"} else ua)
            elif (
                operation == Op.DIVIDE
                and ua == "integer_price"
                and ub == "integer_price_per_price"
            ):
                units.append("price")
            elif (
                operation == Op.MULTIPLY
                and ua == "price"
                and ub == "integer_price_per_price"
            ):
                units.append("integer_price")
            elif (
                operation == Op.DIVIDE
                and ua == ub
                and ua not in {"literal", "unspecified"}
            ):
                units.append("ratio")
            elif ub in {None, "literal"}:
                units.append(ua)
            elif ua == "literal":
                units.append(ub)
            else:
                # Composite units are explicit; a later incompatible
                # comparison is rejected rather than guessed.
                units.append(
                    ua
                    if operation in {Op.ADD, Op.SUBTRACT, Op.REMAINDER}
                    else f"({ua}{'*' if operation == Op.MULTIPLY else '/'}{ub})"
                )
        output_kind = kind(self.output, len(kinds))
        if self.gate and output_kind != "bool":
            raise EncodingError("An admission graph must produce a Boolean gate")
        if not self.gate and output_kind == "bool":
            raise EncodingError("An action-value graph must produce a numeric value")
        if self.output_unit is not None and unit(self.output) != self.output_unit:
            raise EncodingError("Action output unit differs from its declared contract")
        for value, constraint in zip(self.values, self.thresholds):
            if (
                not math.isfinite(value)
                or not constraint.minimum <= value <= constraint.maximum
            ):
                raise EncodingError(f"Threshold outside constraints: {constraint.name}")
            if constraint.integer and value != int(value):
                raise EncodingError(f"Threshold must be integer: {constraint.name}")
        return self

    def parameters(self, candidates, device):
        rows = candidates.tolist() if hasattr(candidates, "tolist") else candidates
        if not rows or not isinstance(rows[0], (list, tuple)):
            rows = [rows]
        for row in rows:
            replace(self, values=tuple(row)).validate()
        return torch.tensor(rows, device=device, dtype=torch.float64)

    def with_arrays(self, instructions, values, *, output=None):
        """Accept optimizer arrays at setup; preserve the atomic input schema."""
        rows = (
            instructions.tolist() if hasattr(instructions, "tolist") else instructions
        )
        values = values.tolist() if hasattr(values, "tolist") else values
        return replace(
            self,
            instructions=tuple(tuple(row) for row in rows),
            values=tuple(values),
            output=self.output if output is None else output,
        ).validate()

    def evaluate(self, inputs, parameters):
        """Evaluate a fixed graph; [B,N] in, [B,N] out (Boolean gates).

        Unknown numeric evidence propagates through comparisons as NaN. Final
        gates map unknown to false; NOT therefore cannot turn missing data into
        permission. Integer inputs are preserved until arithmetic needs real
        values, avoiding float encodings for producer-owned identity labels.
        """
        results = []
        shape = next(iter(inputs.values())).shape if inputs else (len(parameters), 1)

        def operand(label):
            return results[-label - 1] if label < 0 else inputs[self.inputs[label].name]

        for operation, a, b, p in self.instructions:
            operation = Op(operation)
            if operation == Op.VALUE:
                value = parameters[:, p : p + 1].expand(shape)
                if self.thresholds[p].integer:
                    value = value.to(torch.int64)
            else:
                x = operand(a)
                y = operand(b) if b != UNUSED else None
                if operation == Op.ADD:
                    value = x + y
                elif operation == Op.SUBTRACT:
                    value = x - y
                elif operation == Op.MULTIPLY:
                    value = x * y
                elif operation == Op.DIVIDE:
                    value = x / y
                elif operation == Op.REMAINDER:
                    value = x.remainder(y)
                elif operation == Op.ABS:
                    value = x.abs()
                elif operation == Op.ROUND:
                    value = torch.round(x)
                elif operation == Op.TO_BPS:
                    value = x * 10000
                elif operation == Op.FROM_BPS:
                    value = x / 10000
                elif operation == Op.FINITE:
                    value = torch.isfinite(x).to(torch.float64)
                elif operation == Op.NOT:
                    value = 1 - x.to(torch.float64)
                elif operation == Op.AND:
                    value = torch.minimum(x.to(torch.float64), y.to(torch.float64))
                    value = torch.where((x == 0) | (y == 0), 0, value)
                elif operation == Op.OR:
                    value = torch.maximum(x.to(torch.float64), y.to(torch.float64))
                    value = torch.where((x == 1) | (y == 1), 1, value)
                else:
                    if operation == Op.GREATER:
                        flag = x > y
                    elif operation == Op.GREATER_EQUAL:
                        flag = x >= y
                    elif operation == Op.LESS:
                        flag = x < y
                    elif operation == Op.LESS_EQUAL:
                        flag = x <= y
                    else:
                        flag = x == y
                    value = torch.where(
                        torch.isfinite(x) & torch.isfinite(y),
                        flag.to(torch.float64),
                        float("nan"),
                    )
            results.append(value)
        return operand(self.output) == 1 if self.gate else operand(self.output)


class _Expression:
    """Compile-time symbolic operand; never used in the replay hot path."""

    def __init__(self, builder, label):
        self.builder, self.label = builder, label

    def _binary(self, op, other):
        return self.builder.node(op, self, self.builder.value(other))

    def __add__(self, other):
        return self._binary(Op.ADD, other)

    def __radd__(self, other):
        return self.builder.node(Op.ADD, self.builder.value(other), self)

    def __sub__(self, other):
        return self._binary(Op.SUBTRACT, other)

    def __rsub__(self, other):
        return self.builder.node(Op.SUBTRACT, self.builder.value(other), self)

    def __mul__(self, other):
        return self._binary(Op.MULTIPLY, other)

    def __rmul__(self, other):
        return self.builder.node(Op.MULTIPLY, self.builder.value(other), self)

    def __truediv__(self, other):
        return self._binary(Op.DIVIDE, other)

    def __rtruediv__(self, other):
        return self.builder.node(Op.DIVIDE, self.builder.value(other), self)

    def __gt__(self, other):
        return self._binary(Op.GREATER, other)

    def __ge__(self, other):
        return self._binary(Op.GREATER_EQUAL, other)

    def __lt__(self, other):
        return self._binary(Op.LESS, other)

    def __le__(self, other):
        return self._binary(Op.LESS_EQUAL, other)

    def __eq__(self, other):
        return self._binary(Op.EQUAL, other)

    def __and__(self, other):
        return self._binary(Op.AND, other)

    def __or__(self, other):
        return self._binary(Op.OR, other)

    def __invert__(self):
        return self.builder.node(Op.NOT, self)

    def remainder(self, other):
        return self._binary(Op.REMAINDER, other)

    def abs(self):
        return self.builder.node(Op.ABS, self)

    def to_bps(self):
        return self.builder.node(Op.TO_BPS, self)

    def from_bps(self):
        return self.builder.node(Op.FROM_BPS, self)

    @classmethod
    def __torch_function__(cls, func, types, args=(), kwargs=None):
        if func is torch.isfinite:
            return args[0].builder.node(Op.FINITE, args[0])
        if func is torch.round:
            return args[0].builder.node(Op.ROUND, args[0])
        raise EncodingError(f"Tracer has no atomic lowering for {func.__name__}")


class _Builder:
    def __init__(self, inputs, bounds):
        self.inputs, self.bounds = inputs, bounds
        self.rows, self.values, self.thresholds, self.constants = [], [], [], {}

    def value(self, value):
        if isinstance(value, _Expression):
            return value
        key = (type(value), value)
        if key not in self.constants:
            constraint = self.bounds.get(
                key,
                Threshold(
                    f"literal_{len(self.values)}", value, value, type(value) is int
                ),
            )
            self.values.append(value)
            self.thresholds.append(constraint)
            self.rows.append((int(Op.VALUE), UNUSED, UNUSED, len(self.values) - 1))
            self.constants[key] = _Expression(self, -len(self.rows))
        return self.constants[key]

    def node(self, op, a, b=None):
        self.rows.append((int(op), a.label, UNUSED if b is None else b.label, UNUSED))
        return _Expression(self, -len(self.rows))


def trace(function, inputs, *, bounds=None, gate=True, output_unit=None):
    """Lower a pure mathematical rule to editable instruction/value arrays.

    Bounds are explicit by (Python type, literal). Structural clock/scale/ID
    constants stay fixed. Searchable thresholds get documented ranges. Mutated
    instruction graphs still pass the same typed, acyclic validator.
    """
    builder = _Builder(tuple(inputs), bounds or {})
    symbols = {item.name: _Expression(builder, item.label) for item in inputs}
    result = builder.value(function(symbols))
    return Graph(
        tuple(inputs),
        tuple(builder.rows),
        tuple(builder.values),
        tuple(builder.thresholds),
        result.label,
        gate=gate,
        output_unit=output_unit,
    ).validate()
