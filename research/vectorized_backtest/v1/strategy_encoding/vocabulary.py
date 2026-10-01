"""Search-facing class labels and constraints, without importing NumPy/Torch."""

from dataclasses import asdict

from .core import Catalog, Operation, Unit


def describe(catalog: Catalog) -> dict:
    """Return JSON-compatible vocabulary for generators, optimizers and UIs.

    The compiler remains the authority for composed unit inference. A generated
    program must pass compilation before it is a valid objective candidate.
    """
    unary = {
        Operation.INPUT,
        Operation.TO_BPS,
        Operation.NOT,
        Operation.LAG,
        Operation.ROLLING_MIN,
        Operation.ROLLING_MAX,
        Operation.EXIT,
    }
    windows = {Operation.LAG, Operation.ROLLING_MIN, Operation.ROLLING_MAX}
    return {
        "version": "atomic-polars-vocabulary-v1",
        "units": [unit.value for unit in Unit],
        "inputs": [asdict(feature) for feature in catalog.inputs],
        "parameters": [asdict(parameter) for parameter in catalog.parameters],
        "constraints": [asdict(constraint) for constraint in catalog.constraints],
        "operations": [
            {
                "label": int(op),
                "name": op.name,
                "operands": 0
                if op in {Operation.PAD, Operation.PARAMETER}
                else 1
                if op in unary
                else 2,
                "parameter_role": "value"
                if op == Operation.PARAMETER
                else "observation_lookback"
                if op in windows
                else None,
            }
            for op in Operation
        ],
        "bounds": {
            "instructions": 64,
            "parameters": 64,
            "composed_history": catalog.max_lookback,
        },
    }
