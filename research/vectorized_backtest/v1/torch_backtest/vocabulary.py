"""Search-facing operation labels, physical fields, units and dimension rules."""

from research.vectorized_backtest.v1.strategy_encoding import (
    describe as shared_vocabulary,
)

from .compiler import HistoryOperation


def describe(catalog):
    result = shared_vocabulary(catalog)
    result["version"] = "torch-history-vocabulary-v1"
    result["operations"].extend(
        {
            "label": int(operation),
            "name": operation.name,
            "operands": 1,
            "parameter_role": "completed_source_observations",
            "input_constraint": "atomic market field",
            "output_unit": "same as input",
        }
        for operation in HistoryOperation
    )
    result["history_contract"] = {
        "initial_lookback": 12,
        "searchable": True,
        "tensor_view": "N x atomic_fields x history",
        "ordering": "newest first",
        "count": "actual completed source observations, not decision ticks",
    }
    return result
