from decimal import Decimal as D, localcontext

import pytest

from src.trading_runtime.squeeze_ladder_lots import LadderFillFact as F, ladder_lot_exposure


def test_staggered_parent_fills_and_target_exit_keep_independent_exposure():
    facts = (F("p1", "lot-1", "entry", D(34)),
             F("p2", "lot-2", "entry", D(5)),
             F("p3", "lot-3", "entry", D(0)),
             F("t1", "lot-1", "profit_target", D(20)))
    lots = ("lot-1", "lot-2", "lot-3")
    result = ladder_lot_exposure(lots, facts)
    assert [x.remaining for x in result] == [D(14), D(5), D(0)]
    assert sum(x.remaining for x in result) == D(19)
    assert ladder_lot_exposure(lots, facts) == result


def test_exit_cannot_steal_shares_from_a_different_target_lot():
    with pytest.raises(ValueError, match="own acquired"):
        ladder_lot_exposure(("a", "b"),
            (F("p1", "a", "entry", D(10)), F("p2", "b", "entry", D(1)),
             F("t2", "b", "profit_target", D(2))))


def test_repair_fills_preserve_original_lot_identity():
    result = ladder_lot_exposure(("a", "b"),
        (F("p1", "a", "entry", D(10)), F("p2", "b", "entry", D(5)),
         F("repair-stop-b", "b", "protective_stop", D(3))))
    assert [x.remaining for x in result] == [D(10), D(2)]


def test_duplicate_cumulative_order_observation_is_rejected():
    fact = F("p", "a", "entry", D(1))
    with pytest.raises(ValueError, match="duplicate"):
        ladder_lot_exposure(("a", "b"), (fact, fact))


def test_quantity_reduction_is_exact_under_low_external_decimal_precision():
    facts = (F("p", "a", "entry", D("12345678901234567890.123456789012345678")),
             F("t", "a", "profit_target", D("12345678901234567890.123456789012345677")))
    with localcontext() as context:
        context.prec = 4
        result = ladder_lot_exposure(("a", "b"), facts)
    assert result[0].remaining == D("0.000000000000000001")


@pytest.mark.parametrize("quantity", [D("1e20"), D("1e-19"), D("NaN")])
def test_quantities_outside_normalized_journal_domain_fail_closed(quantity):
    with pytest.raises(ValueError):
        ladder_lot_exposure(("a", "b"), (F("p", "a", "entry", quantity),))
