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
    result["v7_contract"] = {
        "source": "certified V6 candle feature bank",
        "shape": "N x H x 2 sides x 5 nearest slots x 11 V6 fields",
        "side_order": ["below/equal", "above"],
        "slot_order": "nearest center first, independent of support/resistance role",
        "mask": "present; false is known, all other absent fields are unknown",
        "history": "slot at each completed candle, not stable level identity",
        "derived_fields": [
            "center/lower/upper distance_bps",
            "center/lower/upper price",
        ],
    }
    return result
