"""Typed operation/input/value genomes, independent of financial execution.

Every coordinate is a declared numeric value or a discrete class/reference ID.
Scalar inputs use nonnegative IDs; negative IDs reference preceding instructions.
ATen programs use their own documented nonnegative node namespace. Topologies
are decoded and validated on CPU, then grouped before compiled vectorized replay.
The candidate representation is data: it never accepts executable Python.
"""

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256

import numpy as np
import torch

from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError

from .atomic_graph import UNUSED, Op
from .genetic_search import Dimension, StrategySpace
from .strategy_one_tensor_policy import PROTECTION_PARAMETERS
from .tensor_program import OPERATIONS, TensorProgram


@dataclass(frozen=True)
class ClassGene:
    component: str
    node: int
    field: str
    allowed: tuple[int, ...]
    default: int
    path: tuple = ()


def _replace_path(tree, path, value):
    for key in path[:-1]:
        tree = tree[key]
    tree[path[-1]] = value


def _paths(tree, path=()):
    if isinstance(tree, dict):
        if "node" in tree:
            yield path + ("node",), tree["node"]
        else:
            for key, value in tree.items():
                yield from _paths(value, path + (key,))
    elif isinstance(tree, (tuple, list)):
        for key, value in enumerate(tree):
            yield from _paths(value, path + (key,))


def _literal_paths(tree, path=()):
    if isinstance(tree, dict):
        if "literal" in tree:
            yield path + ("literal",), tree["literal"]
        else:
            for key, value in tree.items():
                yield from _literal_paths(value, path + (key,))
    elif isinstance(tree, (tuple, list)):
        for key, value in enumerate(tree):
            yield from _literal_paths(value, path + (key,))


def tensor_samples(program):
    """Schema-only setup witnesses, not a market replay or fitness evaluation."""
    defaults = {
        "threshold." + name: value for name, value, _, _ in PROTECTION_PARAMETERS
    }
    return tuple(
        torch.full(
            tuple(spec["shape"]),
            defaults.get(name, 1 if name.endswith(".tick") else 0),
            dtype=getattr(torch, spec["dtype"].removeprefix("torch.")),
        )
        for name, spec in zip(program.names, program.schema)
    )


def _tensor_types(program):
    samples = tensor_samples(program)
    types = []
    units = {}
    input_names = iter(program.names)

    class Capture(torch.fx.Interpreter):
        def run_node(self, node):
            value = super().run_node(node)
            if node.op != "output":
                if not isinstance(value, torch.Tensor):
                    raise ValueError("Policy nodes must produce atomic tensors")
                if node.op == "placeholder":
                    unit = _input_unit(next(input_names))
                else:
                    arguments = []

                    def collect(argument):
                        if isinstance(argument, torch.fx.Node):
                            arguments.append(units[argument])
                        elif isinstance(argument, (tuple, list)):
                            for item in argument:
                                collect(item)

                    collect(node.args)
                    unit = arguments[0] if arguments else "count"
                    operation = str(node.target)
                    if operation.startswith("aten.where") and len(arguments) > 1:
                        unit = arguments[1]
                    elif (
                        operation.startswith("aten.div")
                        and len(arguments) > 1
                        and arguments[0] == arguments[1]
                    ):
                        unit = "ratio"
                    elif operation.startswith(
                        ("aten.arange", "aten.sum", "aten.cumsum")
                    ):
                        unit = "count"
                    if unit == "boolean" and value.dtype != torch.bool:
                        unit = "count"
                if value.dtype == torch.bool:
                    unit = "boolean"
                units[node] = unit
                types.append((str(value.dtype), tuple(value.shape), unit))
            return value

    with torch.no_grad():
        Capture(program.rebuild("cpu")).run(*samples)
    return types


def _tensor_families(name):
    """Closed arity/dtype-compatible operation classes; IDs remain ATen IDs."""
    for family in (
        ("eq.Tensor", "ne.Tensor", "gt.Tensor", "ge.Tensor", "lt.Tensor", "le.Tensor"),
        ("eq.Scalar", "ne.Scalar", "gt.Scalar", "ge.Scalar", "lt.Scalar", "le.Scalar"),
        ("bitwise_and.Tensor", "bitwise_or.Tensor"),
        ("maximum.default", "minimum.default"),
        ("amin.default", "amax.default"),
        ("add.Tensor", "sub.Tensor"),
        ("ceil.default", "floor.default", "round.default", "abs.default"),
    ):
        if name in family:
            return tuple(OPERATIONS.index(item) for item in family)
    return (OPERATIONS.index(name),)


class CategoricalStrategySpace:
    """Flat [B,P] genome plus explicit per-slot class catalog and numeric ABI.

    P is derived from contracts, not fixed at 10/14. Category IDs are sampled
    discretely, never interpolated as real-valued thresholds. Structural shape,
    axis, sentinel and register-output contracts are enumerated as fixed fields.
    Scalar operand/output choices are validated by the full typed graph validator.
    Tensor operand rewiring is limited to identical dtype/shape signatures; only
    source inputs with the same semantic unit can substitute for each other.
    """

    def __init__(self, programs=None):
        self.numeric = StrategySpace()
        self.graphs = self.numeric.graphs
        self.programs = programs or {}
        self.genes, self.fixed, self.tensor_values = [], [], []
        for component, graph in self.graphs.items():
            self._scalar_catalog(component, graph)
        for component, program in self.programs.items():
            self._tensor_catalog(component, program)
        self.base_numeric_count = len(self.numeric.dimensions)
        numeric_dimensions = self.numeric.dimensions + tuple(
            item[3] for item in self.tensor_values
        )
        self.numeric_count = len(numeric_dimensions)
        self.dimensions = numeric_dimensions + tuple(
            Dimension(
                f"{g.component}.{g.node}.{g.field}.{'.'.join(map(str, g.path))}",
                min(g.allowed),
                max(g.allowed),
                True,
                g.default,
            )
            for g in self.genes
        )
        self.low = np.array([d.minimum for d in self.dimensions])
        self.high = np.array([d.maximum for d in self.dimensions])
        self.integer = np.array([d.integer for d in self.dimensions])
        self.default = np.array([d.default for d in self.dimensions], dtype=np.float64)
        self.indices = {d.name: i for i, d in enumerate(self.dimensions)}
        self.entry, self.add, self.actions = (
            self.numeric.entry,
            self.numeric.add,
            self.numeric.actions,
        )
        self.rejected_edits = 0

    def _record(self, gene, reason="Single legal class under the typed grammar"):
        if len(gene.allowed) > 1:
            self.genes.append(gene)
        else:
            self.fixed.append({**asdict(gene), "reason": reason})

    def _scalar_catalog(self, component, graph):
        for index, row in enumerate(graph.instructions):
            operation, a, b, _parameter = row
            if operation == Op.VALUE:
                self.fixed.append(
                    {
                        "component": component,
                        "node": index,
                        "field": "operation/parameter-reference",
                        "reason": "Parameter declaration; value mutability is separately declared by its range",
                    }
                )
                continue
            if operation in (Op.AND, Op.OR):
                operations = (int(Op.AND), int(Op.OR))
            elif operation in (
                Op.GREATER,
                Op.GREATER_EQUAL,
                Op.LESS,
                Op.LESS_EQUAL,
                Op.EQUAL,
            ):
                operations = tuple(
                    map(
                        int,
                        (
                            Op.GREATER,
                            Op.GREATER_EQUAL,
                            Op.LESS,
                            Op.LESS_EQUAL,
                            Op.EQUAL,
                        ),
                    )
                )
            elif operation in (Op.ADD, Op.SUBTRACT, Op.MULTIPLY, Op.DIVIDE):
                operations = tuple(
                    map(int, (Op.ADD, Op.SUBTRACT, Op.MULTIPLY, Op.DIVIDE))
                )
            else:
                operations = (operation,)
            for field, choices, default in (
                ("operation", operations, operation),
                (
                    "a",
                    tuple(range(len(graph.inputs)))
                    + tuple(-i - 1 for i in range(index)),
                    a,
                ),
                (
                    "b",
                    tuple(range(len(graph.inputs)))
                    + tuple(-i - 1 for i in range(index)),
                    b,
                ),
            ):
                if default == UNUSED:
                    self.fixed.append(
                        {
                            "component": component,
                            "node": index,
                            "field": field,
                            "reason": "Unused operand under declared arity",
                        }
                    )
                    continue
                allowed = []
                column = {"operation": 0, "a": 1, "b": 2}[field]
                for choice in choices:
                    rows = list(graph.instructions)
                    changed = list(row)
                    changed[column] = choice
                    rows[index] = tuple(changed)
                    try:
                        replace(graph, instructions=tuple(rows)).validate()
                    except EncodingError:
                        continue
                    allowed.append(choice)
                self._record(
                    ClassGene(component, index, field, tuple(allowed), default)
                )
        allowed = []
        for choice in tuple(range(len(graph.inputs))) + tuple(
            -i - 1 for i in range(len(graph.instructions))
        ):
            try:
                replace(graph, output=choice).validate()
            except EncodingError:
                continue
            allowed.append(choice)
        self._record(ClassGene(component, -1, "output", tuple(allowed), graph.output))
        for index, threshold in enumerate(graph.thresholds):
            if threshold.minimum == threshold.maximum:
                self.fixed.append(
                    {
                        "component": component,
                        "node": index,
                        "field": "value",
                        "value": graph.values[index],
                        "name": threshold.name,
                        "reason": "Fixed encoding/alignment/sentinel literal in source contract",
                    }
                )

    def _tensor_catalog(self, component, program):
        types = _tensor_types(program)
        # Fixed register outputs/layout axes preserve the engine's state ABI.
        self.fixed.append(
            {
                "component": component,
                "field": "output-register-layout",
                "reason": "Engine register shape/dtype/ownership ABI",
            }
        )
        for index, (row, operand) in enumerate(
            zip(program.instructions.tolist(), program.operands)
        ):
            op, label = row
            for path, value in _literal_paths(operand):
                if (
                    component == "protection"
                    and OPERATIONS[op] == "lt.Scalar"
                    and value == 30000
                ):
                    self.tensor_values.append(
                        (
                            component,
                            index,
                            path,
                            Dimension(
                                "maximum_trailing_low_age_ms", 500, 120000, True, 30000
                            ),
                        )
                    )
            self._record(
                ClassGene(
                    component, index, "operation", _tensor_families(OPERATIONS[op]), op
                ),
                "Indexing/layout/reduction signature has one supported operation class",
            )
            for path, default in _paths(operand):
                # Do not mutate gather/scatter/index operands: certified IDs and
                # address ownership are execution contracts, not strategy inputs.
                if OPERATIONS[op].startswith(
                    ("gather.", "scatter_", "arange.", "slice.", "select.", "expand.")
                ):
                    self.fixed.append(
                        {
                            "component": component,
                            "node": index,
                            "field": "reference",
                            "path": path,
                            "reason": "Index/layout address contract",
                        }
                    )
                    continue
                choices = tuple(i for i in range(label) if types[i] == types[default])
                self._record(
                    ClassGene(component, index, "reference", choices, default, path)
                )
            for path, value in _literal_paths(operand):
                if any(
                    c == component and n == index and p == path
                    for c, n, p, _ in self.tensor_values
                ):
                    continue
                self.fixed.append(
                    {
                        "component": component,
                        "node": index,
                        "field": "literal",
                        "path": path,
                        "value": value,
                        "reason": "Shape/axis/arity/alignment/rounding/sentinel contract",
                    }
                )
        # Register positions/types are fixed; the typed expression feeding a
        # register or policy event is a searchable backward-reference class.
        for path, default in _paths(program.output):
            choices = tuple(i for i in range(len(types)) if types[i] == types[default])
            self._record(
                ClassGene(component, -1, "output_reference", choices, default, path)
            )

    def manifest(self):
        return {
            "version": "typed-strategy-genome-v1",
            "shape": ["B", len(self.dimensions)],
            "numeric_dimensions": [
                asdict(d) for d in self.dimensions[: self.numeric_count]
            ],
            "class_genes": [asdict(g) for g in self.genes],
            "fixed_fields": self.fixed,
            "scalar_operation_classes": {int(op): op.name for op in Op},
            "tensor_operation_classes": dict(enumerate(OPERATIONS)),
            "scalar_inputs": {
                name: [asdict(i) for i in g.inputs] for name, g in self.graphs.items()
            },
            "tensor_inputs": {
                name: program.schema for name, program in self.programs.items()
            },
        }

    def _graphs(self, row):
        graphs = dict(self.graphs)
        programs = {
            name: deepcopy(program.manifest())
            for name, program in self.programs.items()
        }
        for offset, (component, node, path, dimension) in enumerate(
            self.tensor_values, self.base_numeric_count
        ):
            value = int(row[offset]) if dimension.integer else float(row[offset])
            _replace_path(programs[component]["operands"][node], path, value)
            programs[component].setdefault("value_overrides", []).append(
                {"node": node, "path": path, "value": value}
            )
        for gene, value in zip(self.genes, row[self.numeric_count :]):
            value = int(value)
            if gene.component in graphs:
                graph = graphs[gene.component]
                if gene.field == "output":
                    graph = replace(graph, output=value)
                else:
                    rows = list(graph.instructions)
                    changed = list(rows[gene.node])
                    changed[{"operation": 0, "a": 1, "b": 2}[gene.field]] = value
                    rows[gene.node] = tuple(changed)
                    graph = replace(graph, instructions=tuple(rows))
                graphs[gene.component] = graph
            else:
                program = programs[gene.component]
                if gene.field == "operation":
                    program["instructions"][gene.node][0] = value
                elif gene.field == "output_reference":
                    _replace_path(program["output"], gene.path, value)
                else:
                    _replace_path(program["operands"][gene.node], gene.path, value)
        for graph in graphs.values():
            graph.validate()
        for name, manifest in programs.items():
            # Rewired programs get their own integrity hash before decoding.
            manifest.pop("sha256", None)
            manifest["sha256"] = sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest()
            program = program_from_manifest(manifest)
            samples = tensor_samples(program)
            with torch.no_grad():
                result = program.rebuild("cpu")(*samples)
                expected = self.programs[name].rebuild("cpu")(
                    *tensor_samples(self.programs[name])
                )
            _check_output(result, expected)
        return graphs, programs

    def repair(self, values):
        rows = np.asarray(values, dtype=np.float64)
        if (
            rows.ndim != 2
            or rows.shape[1] != len(self.dimensions)
            or not np.isfinite(rows).all()
        ):
            raise ValueError(f"Genome must be finite [B,{len(self.dimensions)}]")
        rows = rows.copy()
        rows[:, : self.base_numeric_count] = self.numeric.repair(
            rows[:, : self.base_numeric_count]
        )
        rows[:, self.base_numeric_count : self.numeric_count] = np.rint(
            np.clip(
                rows[:, self.base_numeric_count : self.numeric_count],
                self.low[self.base_numeric_count : self.numeric_count],
                self.high[self.base_numeric_count : self.numeric_count],
            )
        )
        for index, gene in enumerate(self.genes, self.numeric_count):
            if not np.isin(rows[:, index], gene.allowed).all():
                raise ValueError("Unknown/noninteger categorical class ID")
        # Coupled edits can invalidate a previously legal reference. Restore
        # invalid edits deterministically, counting every rejected proposal.
        for row in rows:
            try:
                self._graphs(row)
            except (
                EncodingError,
                ValueError,
                RuntimeError,
                IndexError,
                ZeroDivisionError,
                TypeError,
            ):
                repaired = self.default.copy()
                repaired[: self.numeric_count] = row[: self.numeric_count]
                for index in (
                    np.flatnonzero(
                        row[self.numeric_count :] != self.default[self.numeric_count :]
                    )
                    + self.numeric_count
                ):
                    proposal = repaired.copy()
                    proposal[index] = row[index]
                    try:
                        self._graphs(proposal)
                    except (
                        EncodingError,
                        ValueError,
                        RuntimeError,
                        IndexError,
                        ZeroDivisionError,
                        TypeError,
                    ):
                        self.rejected_edits += 1
                    else:
                        repaired = proposal
                row[:] = repaired
        return rows

    def sample(self, rng, size):
        rows = np.tile(self.default, (size, 1))
        rows[:, : self.numeric_count] = rng.uniform(
            self.low[: self.numeric_count],
            self.high[: self.numeric_count],
            (size, self.numeric_count),
        )
        for row in rows:
            for index in rng.choice(
                len(self.genes), size=min(2, len(self.genes)), replace=False
            ):
                row[self.numeric_count + index] = rng.choice(self.genes[index].allowed)
        return self.repair(rows)

    def offspring(self, rng, parents, diversify=False):
        child = np.where(rng.random(len(self.dimensions)) < 0.5, *parents)
        mutation = rng.random(self.numeric_count) < 0.3
        child[: self.numeric_count] += (
            mutation
            * rng.normal(0, 0.25 if diversify else 0.1, self.numeric_count)
            * (self.high[: self.numeric_count] - self.low[: self.numeric_count])
        )
        for index in rng.choice(
            len(self.genes),
            size=min(3 if diversify else 1, len(self.genes)),
            replace=False,
        ):
            child[self.numeric_count + index] = rng.choice(self.genes[index].allowed)
        return self.repair([child])[0]

    def decode(self, values):
        rows = self.repair(values)
        return {
            "version": "typed-strategy-genome-v1",
            "values": rows.tolist(),
            "candidates": [self.candidate(row) for row in rows],
        }

    def candidate(self, row):
        graphs, programs = self._graphs(row)
        structural = {
            name: {"instructions": g.instructions, "output": g.output}
            for name, g in graphs.items()
        }
        structural.update(
            {
                name: {
                    "instructions": p["instructions"],
                    "operands": p["operands"],
                    "output": p["output"],
                }
                for name, p in programs.items()
            }
        )
        return {
            "topology": sha256(
                json.dumps(structural, sort_keys=True).encode()
            ).hexdigest(),
            "graphs": {name: asdict(g) for name, g in graphs.items()},
            "programs": programs,
            "parameters": self.numeric.decode([row[: self.base_numeric_count]]),
        }


def _input_unit(name):
    if name.endswith(("_id", ".accepted", ".prior_id")):
        return "identity"
    if name.endswith("_ms"):
        return "milliseconds"
    if name.endswith("_int"):
        return "integer_price"
    if name.endswith(("_count", "_groups", "_ordinal")) or name.startswith(
        "threshold."
    ):
        return "count"
    if name.rsplit(".", 1)[-1] in {
        "stop",
        "target",
        "bid",
        "ask",
        "tick",
        "pending_min",
        "earned_min",
        "break_lower",
        "overhead_midpoint",
        "close",
        "lower",
        "upper",
    }:
        return "price"
    return name.rsplit(".", 1)[-1]  # Conservative: no guessed price/count interchange.


def _check_output(actual, expected):
    if isinstance(expected, torch.Tensor):
        if (
            not isinstance(actual, torch.Tensor)
            or actual.shape != expected.shape
            or actual.dtype != expected.dtype
        ):
            raise ValueError(
                "Categorical program violates its output register contract"
            )
    elif isinstance(expected, (tuple, list)):
        if not isinstance(actual, type(expected)) or len(actual) != len(expected):
            raise ValueError("Output layout differs")
        for a, b in zip(actual, expected):
            _check_output(a, b)
    else:
        raise TypeError("Unsupported policy output")


def program_from_manifest(manifest, template=None):
    """Rebind structural shape literals to a session template before rewiring.

    The program name/node ABI must match across sessions; addresses and shape
    literals come from that session, never a prior day's geometry dimensions.
    """
    if (
        manifest.get("version") != "atomic-aten-policy-v1"
        or tuple(manifest.get("operation_classes", ())) != OPERATIONS
    ):
        raise ValueError("Unknown tensor operation vocabulary/version")
    if "sha256" in manifest:
        payload = {key: value for key, value in manifest.items() if key != "sha256"}
        if (
            sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            != manifest["sha256"]
        ):
            raise ValueError("Tensor program manifest hash mismatch")
    if template is None:
        return TensorProgram(
            tuple(i["name"] for i in manifest["inputs"]),
            tuple(manifest["inputs"]),
            torch.tensor(manifest["instructions"], dtype=torch.int64),
            tuple(deepcopy(manifest["operands"])),
            deepcopy(manifest["output"]),
            None,
        )
    if tuple(i["name"] for i in manifest["inputs"]) != template.names or len(
        manifest["instructions"]
    ) != len(template.instructions):
        raise ValueError("Session tensor program ABI differs from categorical template")
    operands = deepcopy(template.operands)
    for changed, target in zip(manifest["operands"], operands):
        for path, value in _paths(changed):
            _replace_path(target, path, value)
    for change in manifest.get("value_overrides", []):
        _replace_path(operands[change["node"]], change["path"], change["value"])
    return TensorProgram(
        template.names,
        template.schema,
        torch.tensor(manifest["instructions"], dtype=torch.int64),
        tuple(operands),
        deepcopy(manifest["output"]),
        None,
    )
