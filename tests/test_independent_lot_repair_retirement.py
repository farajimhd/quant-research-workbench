from types import SimpleNamespace as NS

import pytest

from src.trading_runtime.ibkr_schema import OrderStatus
from src.trading_runtime.independent_lot_repair_retirement import require_cancelled_repair_readback


def packet():
    request = NS(cOID='owned-repair-abc', conid=123, ticker='TEST', side='SELL',
        orderType='STP', price=None, auxPrice=9.5, parentId='', quantity=4.0)
    group = NS(account_id='DU1', orders=(request,), plan=NS(order_slice_ids=('lot-a',)),
        broker_order_ids=('17',), broker_order_request_indexes={'17': 0},
        broker_order_slices={'17': 'lot-a'}, broker_order_roles={'17': 'protective_stop'})
    before = NS(**{key: value for key, value in vars(request).items() if key != 'quantity'},
        orderId='17', account='DU1', filledQuantity=0.0, remainingQuantity=4.0,
        totalSize=4.0, order_status=OrderStatus.SUBMITTED)
    after = NS(**vars(before))
    after.order_status = OrderStatus.CANCELLED
    return group, before, after


def test_exact_cancelled_readback_authorizes_identity_without_mutation():
    group, before, after = packet()
    original = vars(group).copy()
    assert require_cancelled_repair_readback(group, 0, before, after) == '17'
    assert vars(group) == original


@pytest.mark.parametrize('field,value', [
    ('orderId', '18'), ('account', 'OTHER'), ('cOID', 'foreign-repair'),
    ('conid', 456), ('ticker', 'OTHER'), ('side', 'BUY'),
    ('auxPrice', 9.6), ('totalSize', 5.0), ('filledQuantity', 1.0),
    ('remainingQuantity', float('nan')), ('order_status', OrderStatus.SUBMITTED),
    ('order_status', OrderStatus.FILLED),
])
def test_foreign_nonterminal_or_changed_fill_readback_rejected(field, value):
    group, before, after = packet()
    setattr(after, field, value)
    with pytest.raises(ValueError):
        require_cancelled_repair_readback(group, 0, before, after)


def test_missing_readback_and_unowned_request_are_rejected():
    group, before, after = packet()
    with pytest.raises(ValueError, match='readback'):
        require_cancelled_repair_readback(group, 0, before, None)
    group.broker_order_request_indexes['17'] = 1
    with pytest.raises(ValueError, match='ownership'):
        require_cancelled_repair_readback(group, 0, before, after)
