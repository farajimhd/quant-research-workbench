from decimal import Decimal

import pytest

from src.trading_runtime.journal_decimal import (
    decimal_38_18, source_float_from_decimal,
)


def test_source_float_has_exact_decimal_wire_and_roundtrip():
    for value in (0.01, 5.0, 10.02, 1.2345678901234567):
        wire = decimal_38_18(value, source_float=True, positive=True)
        assert source_float_from_decimal(wire, positive=True) == value
        assert len(wire.split(".")[1]) == 18


def test_decimal_contract_rejects_precision_loss_and_nonfinite_values():
    for value in (float("nan"), float("inf"), 1e-19):
        with pytest.raises(ValueError):
            decimal_38_18(value, source_float=True, positive=True)
    with pytest.raises(ValueError):
        decimal_38_18(Decimal("100000000000000000000"))
    with pytest.raises(ValueError):
        source_float_from_decimal("1.000000000000000001", positive=True)
    with pytest.raises(ValueError):
        decimal_38_18(True)
