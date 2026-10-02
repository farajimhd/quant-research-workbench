"""Atomic multidimensional policy programs and their bounded search values.

The engine supplies individual causal register/market lanes. This adapter only
flattens named lanes and reconstructs output registers; the executable policy
itself is rebuilt from atomic operation labels and operand references.
"""

from dataclasses import fields

import torch

from .strategy_one import advance_protection, observe_resistance
from .tensor_program import TensorProgram

PROTECTION_PARAMETERS = (
    ("group_size", 3, 1, 12),
    ("first_target_breaks", 3, 0, 24),
    ("second_target_breaks", 5, 0, 48),
    ("first_ordinal", 3, 1, 12),
    ("second_ordinal", 2, 1, 12),
    ("last_ordinal", 1, 1, 12),
)


class AtomicReducer:
    def __init__(self, kind, shape, device, values=None, program=None):
        if kind not in ("resistance", "protection"):
            raise ValueError("Unknown reducer contract")
        self.kind, self.program = kind, program
        self.checked = False
        if program is not None:
            # Reconstruction validates primitive operation labels and backward
            # references. Never accept an arbitrary Python executable policy.
            program.module = program.rebuild(device)
        self.values = (
            self.checked_values(values, shape[0]) if kind == "protection" else []
        )
        if kind == "protection":
            self.theta = torch.tensor(self.values, dtype=torch.int64, device=device)
            self.parameters = tuple(
                self.theta[:, index : index + 1].expand(shape)
                for index in range(len(PROTECTION_PARAMETERS))
            )
        else:
            self.parameters = ()

    @staticmethod
    def checked_values(values, batch):
        rows = [row[1] for row in PROTECTION_PARAMETERS] if values is None else values
        rows = rows.tolist() if hasattr(rows, "tolist") else rows
        if not rows or not isinstance(rows[0], (list, tuple)):
            rows = [rows] * batch
        if len(rows) != batch:
            raise ValueError("One protection parameter row is required per candidate")
        for row in rows:
            if len(row) != len(PROTECTION_PARAMETERS):
                raise ValueError("Protection values differ from declared constraints")
            for value, (name, _, minimum, maximum) in zip(row, PROTECTION_PARAMETERS):
                if (
                    not isinstance(value, (int, float))
                    or value != int(value)
                    or not minimum <= value <= maximum
                ):
                    raise ValueError(f"Invalid integer policy threshold: {name}")
            if row[1] > row[2]:
                raise ValueError("Target breakpoint thresholds must be ordered")
        return [[int(value) for value in values] for values in rows]

    def update(self, values):
        """Validate at the objective boundary, then copy into captured buffers."""
        rows = self.checked_values(values, len(self.theta))
        self.theta.copy_(
            torch.tensor(rows, dtype=torch.int64, device=self.theta.device)
        )
        self.values = rows

    def __call__(self, state, evidence):
        state_fields = tuple(field.name for field in fields(state))
        input_fields = tuple(sorted(evidence))
        args = tuple(getattr(state, name) for name in state_fields)
        args += tuple(evidence[name] for name in input_fields) + self.parameters
        names = tuple("state." + name for name in state_fields)
        names += tuple("evidence." + name for name in input_fields)
        names += (
            tuple("threshold." + row[0] for row in PROTECTION_PARAMETERS)
            if self.parameters
            else ()
        )
        if (
            self.program is not None
            and not self.checked
            and (
                self.program.names != names
                or any(
                    tuple(spec["shape"]) != tuple(value.shape)
                    or spec["dtype"] != str(value.dtype)
                    for spec, value in zip(self.program.schema, args)
                )
            )
        ):
            raise ValueError(
                "Atomic reducer differs from its named input/shape/dtype contract"
            )
        if self.program is None:
            cls, count = type(state), len(state_fields)
            function = (
                advance_protection if self.kind == "protection" else observe_resistance
            )

            def lower(*lanes):
                registers = cls(*lanes[:count])
                x = dict(zip(input_fields, lanes[count : count + len(input_fields)]))
                result = function(registers, x, *lanes[count + len(input_fields) :])
                proposed, *events = result
                return tuple(getattr(proposed, name) for name in state_fields), tuple(
                    events
                )

            self.program = TensorProgram.lower(lower, names, args)
        registers, events = self.program.module(*args)
        if not self.checked:
            if len(registers) != len(state_fields) or any(
                value.shape != getattr(state, name).shape
                or value.dtype != getattr(state, name).dtype
                for name, value in zip(state_fields, registers)
            ):
                raise ValueError("Atomic policy returned incompatible state registers")
            self.checked = True
        return type(state)(*registers), *events

    def manifest(self):
        if self.program is None:
            raise RuntimeError("Lower the fixed policy before exporting its array")
        return {
            **self.program.manifest(),
            "search_parameters": PROTECTION_PARAMETERS if self.parameters else (),
            "values": self.values if self.parameters else (),
        }
