"""The shared strategy-array ABI lowered to Torch tensor operations.

All values, including Boolean intermediates, have shape [B,N]. Boolean unknowns
use NaN, so NOT/AND/OR retain the Polars compiler's three-valued logic. Final
proposal flags are always 0/1. No instruction dispatch depends on tensor values.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import IntEnum
from numbers import Integral

import torch

from research.vectorized_backtest.v3.torch_backtest.encoding.core import (
    AtomicInput,
    Catalog,
    EncodingError,
    Program,
    Unit,
)
from research.vectorized_backtest.v3.torch_backtest.encoding.core import Operation as Op
from research.vectorized_backtest.v3.torch_backtest.encoding.core import (
    compile_strategy as validate_contract,
)

ACTION_KINDS = (Op.ENTER, Op.EXIT, Op.ADD_POSITION, Op.SET_STOP, Op.SET_TARGET)


class HistoryOperation(IntEnum):
    """Actual completed source observations, independent of strategy sampling.

    Parameter slot contains a searchable positive integer bar/indicator count.
    Lag k excludes the current observation; rolling k includes the current one.
    Operand a is an atomic market input; operand b must be UNUSED.
    """

    BAR_LAG = 50
    BAR_MIN = 51
    BAR_MAX = 52
    BAR_MEAN = 53
    BAR_SUM = 54


def _finite(value):
    return torch.where(torch.isfinite(value), value, float("nan"))


@dataclass(frozen=True)
class TorchStrategy:
    """A checked instruction graph with fixed topology and temporal buffer sizes.

    Market inputs can broadcast from [N] to [B,N]; account inputs are [B,N].
    Parameter values are [B,P], one independent candidate/account per batch.
    Source lookbacks are searchable within their declared maximum. Changing an
    observation-ring lookback changes topology and requires a new compiler.
    """

    program: Program
    catalog: Catalog
    contract: object
    nodes: tuple
    temporal_sizes: tuple
    source_windows: tuple = ()

    @property
    def dependencies(self):
        return self.contract.dependencies

    @property
    def fingerprint(self):
        return self.contract.fingerprint

    def parameters(self, values, device):
        """Validate candidate values on CPU before transfer or graph execution."""
        rows = values.tolist() if hasattr(values, "tolist") else values
        if not rows and not self.catalog.parameters:
            rows = [[]]
        elif not rows:
            raise EncodingError("At least one candidate is required")
        if not isinstance(rows[0], (list, tuple)):
            rows = [rows]
        windows = {Op.LAG, Op.ROLLING_MIN, Op.ROLLING_MAX}
        for row in rows:
            checked = replace(self.program, values=tuple(row))
            compile_strategy(checked, self.catalog)
            for operation, _, _, parameter in self.nodes:
                if (
                    operation in windows
                    and row[parameter] != self.program.values[parameter]
                ):
                    raise EncodingError("Lookback changes require a new Torch compiler")
        return torch.tensor(rows, dtype=torch.float64, device=device)

    def history(self, batch, listings, device):
        # One bounded ring per temporal operand; no dataframe history rebuilding.
        return tuple(
            tuple(
                torch.full(
                    (size, batch, listings),
                    float("nan"),
                    dtype=torch.float64,
                    device=device,
                )
                for _ in range(operands)
            )
            for size, operands in self.temporal_sizes
        )

    def evaluate(
        self, inputs, parameters, history, cursor, observe, source_windows=None
    ):
        """Return [B,N,10]: flag/value pairs in ACTION_KINDS order.

        `observe` is a device Boolean scalar. On a broker-only boundary temporal
        rings retain their prior contents and the caller retains its cursor.
        Shapes and units were checked once before entering this hot path.
        """
        shape = next(iter(inputs.values())).shape
        zero = torch.zeros(shape, dtype=torch.float64, device=parameters.device)
        nan = torch.full_like(zero, float("nan"))
        proposals = [zero, nan, zero, nan, zero, nan, zero, nan, zero, nan]
        results = []
        temporal_index = 0

        def operand(label):
            return results[-label - 1] if label < 0 else inputs[label]

        for node_index, (operation, a, b, parameter) in enumerate(self.nodes):
            if operation == Op.PARAMETER:
                results.append(parameters[:, parameter : parameter + 1].expand(shape))
                continue
            x = operand(a)
            if isinstance(operation, HistoryOperation):
                # [N,H] -> [B,N,H], without a full [T,N,H,F] materialization.
                window = (
                    source_windows[node_index].unsqueeze(0).expand(shape[0], -1, -1)
                )
                offsets = torch.arange(window.shape[-1], device=parameters.device)
                count = parameters[:, parameter : parameter + 1, None]
                selected = (
                    offsets == count
                    if operation == HistoryOperation.BAR_LAG
                    else offsets < count
                )
                if operation in {HistoryOperation.BAR_MIN, HistoryOperation.BAR_MAX}:
                    extreme = (
                        float("inf")
                        if operation == HistoryOperation.BAR_MIN
                        else -float("inf")
                    )
                    masked = torch.where(selected, window, extreme)
                    value = (
                        masked.amin(dim=-1)
                        if operation == HistoryOperation.BAR_MIN
                        else masked.amax(dim=-1)
                    )
                else:
                    value = torch.where(selected, window, 0.0).sum(dim=-1)
                    if operation == HistoryOperation.BAR_MEAN:
                        value = value / count.squeeze(-1)
                value = _finite(value)
            elif operation == Op.INPUT:
                value = x
            elif operation in {Op.LAG, Op.ROLLING_MIN, Op.ROLLING_MAX}:
                buffer = history[temporal_index][0]
                temporal_index += 1
                index = cursor.remainder(buffer.shape[0]).reshape(1)
                old = buffer.index_select(0, index).squeeze(0)
                buffer.index_copy_(0, index, torch.where(observe, x, old).unsqueeze(0))
                if operation == Op.LAG:
                    value = old
                elif operation == Op.ROLLING_MIN:
                    value = buffer.amin(dim=0)
                else:
                    value = buffer.amax(dim=0)
            elif operation == Op.TO_BPS:
                value = _finite(x * 10000)
            elif operation == Op.NOT:
                value = torch.where(torch.isfinite(x), 1 - x, nan)
            elif operation == Op.EXIT:
                proposals[2] = (x == 1).to(torch.float64)
                results.append(None)
                continue
            else:
                y = operand(b)
                known = torch.isfinite(x) & torch.isfinite(y)
                if operation == Op.AND:
                    value = torch.where(
                        (x == 0) | (y == 0),
                        zero,
                        torch.where((x == 1) & (y == 1), 1.0, nan),
                    )
                elif operation == Op.OR:
                    value = torch.where(
                        (x == 1) | (y == 1),
                        1.0,
                        torch.where((x == 0) & (y == 0), zero, nan),
                    )
                elif operation in {Op.CROSS_ABOVE, Op.CROSS_BELOW}:
                    bx, by = history[temporal_index]
                    temporal_index += 1
                    ix = torch.zeros(1, dtype=torch.int64, device=parameters.device)
                    previous_x = bx.index_select(0, ix).squeeze(0)
                    previous_y = by.index_select(0, ix).squeeze(0)
                    previous_known = torch.isfinite(previous_x) & torch.isfinite(
                        previous_y
                    )
                    comparison = (
                        (x > y) & (previous_x <= previous_y)
                        if operation == Op.CROSS_ABOVE
                        else (x < y) & (previous_x >= previous_y)
                    )
                    # Kleene AND: a false known current comparison stays false
                    # even when the preceding observation is unavailable.
                    current = x > y if operation == Op.CROSS_ABOVE else x < y
                    prior = (
                        previous_x <= previous_y
                        if operation == Op.CROSS_ABOVE
                        else previous_x >= previous_y
                    )
                    value = torch.where(
                        (known & ~current) | (previous_known & ~prior),
                        zero,
                        torch.where(
                            known & previous_known, comparison.to(torch.float64), nan
                        ),
                    )
                    bx.index_copy_(
                        0, ix, torch.where(observe, x, previous_x).unsqueeze(0)
                    )
                    by.index_copy_(
                        0, ix, torch.where(observe, y, previous_y).unsqueeze(0)
                    )
                elif operation in {
                    Op.GREATER,
                    Op.GREATER_EQUAL,
                    Op.LESS,
                    Op.LESS_EQUAL,
                    Op.EQUAL,
                }:
                    comparison = (
                        x > y
                        if operation == Op.GREATER
                        else x >= y
                        if operation == Op.GREATER_EQUAL
                        else x < y
                        if operation == Op.LESS
                        else x <= y
                        if operation == Op.LESS_EQUAL
                        else x == y
                    )
                    value = torch.where(known, comparison.to(torch.float64), nan)
                elif operation in ACTION_KINDS:
                    offset = ACTION_KINDS.index(operation) * 2
                    allowed = (
                        (y >= 0) & (y <= 1)
                        if operation in {Op.ENTER, Op.ADD_POSITION}
                        else y > 0
                    )
                    gate = (x == 1) & allowed & torch.isfinite(y)
                    proposals[offset] = gate.to(torch.float64)
                    proposals[offset + 1] = torch.where(gate, y, nan)
                    results.append(None)
                    continue
                else:
                    value = (
                        x + y
                        if operation == Op.ADD
                        else x - y
                        if operation == Op.SUBTRACT
                        else x * y
                        if operation == Op.MULTIPLY
                        else x / y
                    )
                    if operation in {Op.DIVIDE, Op.RELATIVE_GAP, Op.GAP_BPS}:
                        value = torch.where(y != 0, value, nan)
                    if operation in {Op.RELATIVE_GAP, Op.GAP_BPS}:
                        value = value - 1
                        if operation == Op.GAP_BPS:
                            value = value * 10000
                    value = _finite(value)
            results.append(value)
        return torch.stack(proposals, dim=-1)


def compile_strategy(program: Program, catalog: Catalog) -> TorchStrategy:
    """Reuse the authoritative ABI/unit validator; no Polars execution in replay."""
    inputs = {f.label: f for f in catalog.inputs}
    lowered = list(program.instructions)
    synthetic = []
    windows = []
    for index, (row, active) in enumerate(zip(program.instructions, program.mask)):
        if len(row) != 4:
            raise EncodingError("Instruction shape must be [L,4]")
        if active and row[0] in set(HistoryOperation):
            if any(not isinstance(x, Integral) or isinstance(x, bool) for x in row):
                raise EncodingError("Source-history labels must be integers")
            operation, label, unused, parameter = row
            if (
                label not in inputs
                or unused != 2**31 - 1
                or not 0 <= parameter < len(catalog.parameters)
            ):
                raise EncodingError(
                    "Source history requires an atomic input and count parameter"
                )
            feature, spec = inputs[label], catalog.parameters[parameter]
            if feature.unit == Unit.BOOLEAN and operation != HistoryOperation.BAR_LAG:
                raise EncodingError("Source-history aggregates require a numeric field")
            if (
                feature.source == "state"
                or spec.unit != Unit.COUNT
                or not spec.integer
                or not 1 <= spec.minimum <= spec.maximum <= catalog.max_lookback
                or spec.maximum != int(spec.maximum)
            ):
                raise EncodingError(
                    "Source history needs a market field and bounded integer range"
                )
            synthetic_label = 2**31 - 2 - index
            if synthetic_label in inputs:
                raise EncodingError(
                    "Atomic label collides with reserved history lowering labels"
                )
            synthetic.append(
                AtomicInput(
                    synthetic_label,
                    f"source-window-{index}",
                    f"__source_window_{index}",
                    feature.unit,
                )
            )
            lowered[index] = (int(Op.INPUT), synthetic_label, 2**31 - 1, 2**31 - 1)
            maximum = int(spec.maximum) + (operation == HistoryOperation.BAR_LAG)
            windows.append(
                (index, HistoryOperation(operation), feature, parameter, maximum)
            )
    contract = validate_contract(
        replace(program, instructions=tuple(lowered)),
        replace(catalog, inputs=catalog.inputs + tuple(synthetic)),
    )
    if windows:
        dependencies = {
            f.label: f
            for f in contract.dependencies
            if not f.column.startswith("__source_window_")
        }
        dependencies.update({f.label: f for _, _, f, _, _ in windows})
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    # Theta is float64 on device. Normalize JSON number types so
                    # initial integer 12 and a round-tripped 12.0 identify the
                    # same policy when a captured runner updates its parameters.
                    "program": replace(
                        program, values=tuple(float(v) for v in program.values)
                    ).to_dict(),
                    "catalog": asdict(catalog),
                    "backend": "torch-source-windows-v1",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        contract = replace(
            contract,
            dependencies=tuple(dependencies[k] for k in sorted(dependencies)),
            fingerprint=fingerprint,
            required_columns=tuple(
                sorted(
                    set(catalog.group_columns)
                    | {catalog.clock_column}
                    | {
                        column
                        for f in dependencies.values()
                        for column in (f.column, f.valid_column, f.available_at_column)
                        if column
                    }
                )
            ),
        )
    nodes = tuple(
        (
            HistoryOperation(row[0]) if row[0] in set(HistoryOperation) else Op(row[0]),
            *row[1:],
        )
        for row, active in zip(program.instructions, program.mask)
        if active
    )
    temporal = []
    for operation, _, _, parameter in nodes:
        if operation in {Op.LAG, Op.ROLLING_MIN, Op.ROLLING_MAX}:
            temporal.append((int(program.values[parameter]), 1))
        elif operation in {Op.CROSS_ABOVE, Op.CROSS_BELOW}:
            temporal.append((1, 2))
    return TorchStrategy(
        program, catalog, contract, nodes, tuple(temporal), tuple(windows)
    )
