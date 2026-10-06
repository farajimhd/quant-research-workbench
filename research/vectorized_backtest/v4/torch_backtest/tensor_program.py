"""An auditable atomic ATen program for multidimensional policy reducers.

Scalar gates use ``atomic_graph``. Resistance witnesses and protection rules
also need gather, prefix scans and reductions over stable level identities.
This IR lowers those calculations to individual ATen operations. No opcode
means 'Strategy 1', 'manage position', or an entire Python policy callback.

The instruction array contains operation labels and backward node references;
the operand tree records argument positions, scalar values and reduction axes.
Inputs are individual tensor fields, never a bundled market/state dictionary.
Structural shape/axis constants cannot be optimized. Searchable values must
be supplied as named input lanes with their independently validated bounds.
"""

import json
from dataclasses import dataclass
from hashlib import sha256

import torch
from torch.fx.experimental.proxy_tensor import make_fx

# Closed operation vocabulary: loading a program never evaluates Python source.
# These are primitive arithmetic, selection, layout and reduction operations.
OPERATIONS = (
    "abs.default",
    "add.Tensor",
    "arange.default",
    "arange.start",
    "bitwise_and.Tensor",
    "bitwise_not.default",
    "bitwise_or.Tensor",
    "clamp_min.default",
    "clone.default",
    "cumsum.default",
    "div.Tensor",
    "div.Tensor_mode",
    "eq.Scalar",
    "eq.Tensor",
    "expand.default",
    "floor.default",
    "full_like.default",
    "ge.Scalar",
    "ge.Tensor",
    "gt.Scalar",
    "gt.Tensor",
    "gather.default",
    "isfinite.default",
    "le.Scalar",
    "le.Tensor",
    "lt.Scalar",
    "lt.Tensor",
    "maximum.default",
    "minimum.default",
    "mul.Tensor",
    "ne.Scalar",
    "ne.Tensor",
    "ones_like.default",
    "remainder.Scalar",
    "remainder.Tensor",
    "round.default",
    "scatter_reduce.two",
    "scatter_reduce_.two",
    "select.int",
    "slice.Tensor",
    "sub.Tensor",
    "sum.dim_IntList",
    "any.dim",
    "amin.default",
    "amax.default",
    "unsqueeze.default",
    "where.self",
    "zeros_like.default",
    "_to_copy.default",
    "ceil.default",
    "floor_divide.default",
    "scalar_tensor.default",
)


def _operation(name):
    namespace, overload = name.split(".", 1)
    return getattr(getattr(torch.ops.aten, namespace), overload)


@dataclass
class TensorProgram:
    """Fixed topology, atomic input schema and reconstructible operand trees."""

    names: tuple[str, ...]
    schema: tuple[dict, ...]
    instructions: torch.Tensor  # [L,2]: operation label, output node label.
    operands: tuple[dict, ...]
    output: object
    module: torch.fx.GraphModule

    @classmethod
    def lower(cls, function, names, samples):
        if len(names) != len(samples) or len(set(names)) != len(names):
            raise ValueError(
                "Atomic tensor inputs need unique names and one tensor each"
            )
        # CPU trace is setup only. It never observes a policy-dependent GPU
        # value during replay, and every traced branch is structural topology.
        module = make_fx(function)(*(value.detach().cpu().clone() for value in samples))
        labels, rows, operands = {}, [], []
        output = None

        def encode(value):
            if isinstance(value, torch.fx.Node):
                return {"node": labels[value]}
            if isinstance(value, (list, tuple)):
                return {
                    "tuple" if isinstance(value, tuple) else "list": [
                        encode(v) for v in value
                    ]
                }
            if isinstance(value, dict):
                return {"dict": {key: encode(v) for key, v in value.items()}}
            if isinstance(value, torch.dtype):
                return {"dtype": str(value).removeprefix("torch.")}
            if isinstance(value, torch.device):
                # Device comes from runtime tensors, not the CPU tracing host.
                return {"device": True}
            if value is torch.strided:
                return {"layout": "strided"}
            if value is None or isinstance(value, (bool, int, float, str)):
                if isinstance(value, float) and not torch.isfinite(torch.tensor(value)):
                    return {
                        "nonfinite": "inf"
                        if value > 0
                        else "-inf"
                        if value < 0
                        else "nan"
                    }
                return {"literal": value}
            raise ValueError(f"Unsupported atomic operand: {type(value).__name__}")

        placeholders = 0
        for node in module.graph.nodes:
            if node.op == "placeholder":
                labels[node] = placeholders
                placeholders += 1
            elif node.op == "call_function":
                name = str(node.target).removeprefix("aten.")
                if name not in OPERATIONS:
                    raise ValueError(
                        f"Atomic operation needs an explicit contract: {name}"
                    )
                node_label = len(names) + len(rows)
                operands.append(
                    {"args": encode(node.args), "kwargs": encode(node.kwargs)}
                )
                rows.append((OPERATIONS.index(name), node_label))
                labels[node] = node_label
            elif node.op == "output":
                output = encode(node.args[0])
            else:
                raise ValueError(f"Policy lowering forbids opaque node kind: {node.op}")
        if placeholders != len(names):
            raise ValueError("Trace dropped or invented an atomic input")
        schema = tuple(
            {"name": name, "dtype": str(value.dtype), "shape": tuple(value.shape)}
            for name, value in zip(names, samples)
        )
        program = cls(
            tuple(names),
            schema,
            torch.tensor(rows, dtype=torch.int64),
            tuple(operands),
            output,
            module,
        )
        # Rebuild from the same serialized IR used for inspection/search. The
        # executable is not the original function or the original FX trace.
        program.module = program.rebuild(samples[0].device)
        return program

    def manifest(self):
        value = {
            "version": "atomic-aten-policy-v1",
            "operation_classes": OPERATIONS,
            "inputs": self.schema,
            "instructions": self.instructions.tolist(),
            "operands": self.operands,
            "output": self.output,
        }
        value["sha256"] = sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        return value

    def rebuild(self, device):
        graph = torch.fx.Graph()
        nodes = [graph.placeholder(name.replace(".", "_")) for name in self.names]

        def decode(value):
            if "node" in value:
                label = value["node"]
                if type(label) is not int or not 0 <= label < len(nodes):
                    raise ValueError("Atomic operands must reference earlier nodes")
                return nodes[label]
            if "tuple" in value:
                return tuple(decode(v) for v in value["tuple"])
            if "list" in value:
                return [decode(v) for v in value["list"]]
            if "dict" in value:
                return {key: decode(v) for key, v in value["dict"].items()}
            if "dtype" in value:
                return getattr(torch, value["dtype"])
            if "device" in value:
                return torch.device(device)
            if "layout" in value:
                if value["layout"] != "strided":
                    raise ValueError("Policy tensors require strided layout")
                return torch.strided
            if "nonfinite" in value:
                return float(value["nonfinite"])
            return value["literal"]

        if len(self.instructions) != len(self.operands):
            raise ValueError("Instruction and operand counts differ")
        for row, operand in zip(self.instructions.tolist(), self.operands):
            operation, label = row
            if not 0 <= operation < len(OPERATIONS) or label != len(nodes):
                raise ValueError("Invalid atomic operation/output label")
            nodes.append(
                graph.call_function(
                    _operation(OPERATIONS[operation]),
                    decode(operand["args"]),
                    decode(operand["kwargs"]),
                )
            )
        graph.output(decode(self.output))
        graph.lint()
        return torch.fx.GraphModule({}, graph)
