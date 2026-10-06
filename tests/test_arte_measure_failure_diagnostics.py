"""Failure evidence must preserve the strict typed Decimal numeric contract."""
from decimal import Decimal, localcontext
import math
from types import SimpleNamespace

import pytest

from src.trading_runtime import arte_journal_projection as projection
from src.trading_runtime.portfolio import PortfolioManagementEngine


def test_real_portfolio_float_dust_rejection_has_exact_bounded_evidence():
    engine = PortfolioManagementEngine.__new__(PortfolioManagementEngine)
    engine.reservations = {}
    engine.allocations = {}
    peak = 10328.515000000001
    state = SimpleNamespace(
        summary=SimpleNamespace(netliquidation=math.nextafter(peak, -math.inf),
                                availablefunds=0.0, buyingpower=0.0),
        positions={}, peak_net_liquidation=peak, realized_pnl_today=0.0,
        profile=SimpleNamespace(account_id="SIM-01-REPLAY"),
    )
    value = engine._metrics(state)["drawdown"]
    assert repr(value) == "1.8189894035458565e-12"
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(value, projection._MEASURE_SCALE,
                                  field="protection.drawdown")
    message = str(caught.value)
    assert message.startswith("Number cannot fit typed Decimal(38) losslessly for protection.drawdown")
    assert "value_type=float" in message
    assert f"value_repr={repr(value)}" in message
    assert "scale_repr=Decimal('1E-18')" in message
    assert f"float_hex={value.hex()}" in message
    assert len(message) < 512


@pytest.mark.parametrize("value,scale,expected", [
    (0.0, Decimal("1E-18"), "0.000000000000000000"),
    (1.25, Decimal("1E-18"), "1.250000000000000000"),
    (Decimal("1E-12"), Decimal("1E-18"), "0.000000000001000000"),
    (-7, Decimal("1E-18"), "-7.000000000000000000"),
    ("2.5", Decimal("1E-18"), "2.500000000000000000"),
    (Decimal("123.4567890123"), Decimal("1E-10"), "123.4567890123"),
])
def test_valid_returns_are_unchanged(value, scale, expected):
    with localcontext() as context:
        context.prec = 3
        assert projection._exact_decimal(value, scale) == expected


@pytest.mark.parametrize("value,prefix", [
    (Decimal("1E-19"), "Number cannot fit typed Decimal(38) losslessly"),
    (1e-19, "Number cannot fit typed Decimal(38) losslessly"),
    (Decimal("1E20"), "Number exceeds typed Decimal(38) width"),
    (Decimal("1E100"), "Number cannot fit typed Decimal(38) precision"),
    (Decimal("NaN"), "Number cannot fit typed Decimal(38) losslessly"),
    (Decimal("sNaN"), "Number cannot fit typed Decimal(38) precision"),
    (Decimal("Infinity"), "Number cannot fit typed Decimal(38) precision"),
    (float("inf"), "Number cannot fit typed Decimal(38) precision"),
    (float("-inf"), "Number cannot fit typed Decimal(38) precision"),
    (float("nan"), "Number cannot fit typed Decimal(38) losslessly"),
])
def test_invalid_values_remain_rejected(value, prefix):
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(value, projection._MEASURE_SCALE, field="protection.drawdown")
    message = str(caught.value)
    assert message.startswith(prefix + " for protection.drawdown")
    assert len(message) < 512
    if type(value) is float and not math.isfinite(value):
        assert "float_hex=" not in message


@pytest.mark.parametrize("value", [10**10000, Decimal("1E999999"), Decimal("1." + "1" * 10000)], ids=["huge-int", "huge-exponent", "huge-coefficient"])
def test_large_builtin_failure_evidence_is_bounded(value):
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(value, projection._MEASURE_SCALE)
    assert len(str(caught.value)) < 512


class HostileValue:
    def __str__(self):
        raise ValueError("private arbitrary payload")

    def __repr__(self):
        raise AssertionError("repr must not execute")


def test_unknown_object_repr_and_exception_payload_are_not_exposed():
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(HostileValue(), projection._MEASURE_SCALE)
    message = str(caught.value)
    assert message.startswith("Number cannot fit typed Decimal(38) precision")
    assert "value_type=HostileValue" in message
    assert "value_repr=<not a builtin numeric scalar>" in message
    assert "private arbitrary payload" not in message


class HostileDecimal(Decimal):
    def __str__(self):
        raise ValueError("private Decimal subclass payload")

    def __repr__(self):
        raise AssertionError("subclass repr must not execute")


def test_numeric_subclass_is_not_given_repr_authority():
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(HostileDecimal("1"), projection._MEASURE_SCALE)
    assert "value_type=HostileDecimal" in str(caught.value)
    assert "private Decimal subclass payload" not in str(caught.value)


def test_strings_are_not_echoed_even_when_original_conversion_rejects():
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal("sensitive arbitrary text", projection._MEASURE_SCALE)
    assert "value_type=str" in str(caught.value)
    assert "sensitive arbitrary text" not in str(caught.value)


def test_diagnostic_fault_does_not_replace_original_numeric_error(monkeypatch):
    def broken(_):
        raise RuntimeError("unexpected diagnostic fault")
    monkeypatch.setattr(projection.math, "isfinite", broken)
    with pytest.raises(ValueError) as caught:
        projection._exact_decimal(1e-19, projection._MEASURE_SCALE)
    assert str(caught.value).startswith("Number cannot fit typed Decimal(38) losslessly")
    assert str(caught.value).endswith("[numeric diagnostic unavailable]")


def test_arbitrary_scale_has_no_repr_authority():
    assert "scale_repr=<not a builtin numeric scalar>" in projection._decimal_failure_diagnostic(1, HostileValue())

