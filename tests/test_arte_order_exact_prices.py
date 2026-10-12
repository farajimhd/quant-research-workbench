from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.arte_order_exact_prices import ExactPriceOrderCommandBatch, seal_exact_order_command_batch
from src.trading_runtime.arte_journal_writer import _wire_row
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.trading_price_rows import restore_exact_price_row


def request_and_scope():
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    price = 9.989999771118164
    request = OrderRequest(acctId='DU1', conid=123, cOID='stop-1', ticker='AAA',
        orderType='STP', side='SELL', quantity=5, auxPrice=price)
    scope = dict(run_id='backtest:exact-order', run_month=date(2026, 8, 1),
        attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id='00000000-0000-0000-0000-000000000000', sequence=1,
        source_cursor='command', run_status='running', command_id='command-1',
        created_at=at, recorded_at=at, strategy_id='program', strategy_revision=1)
    return request, scope


def test_exact_order_projection_keeps_prices_and_nonprice_instructions():
    request, scope = request_and_scope()
    with pytest.raises(ValueError, match='losslessly for order.aux_price'):
        order_command_batch(request, **scope)
    unit = order_command_batch(request, **scope, exact_prices=True)
    assert type(unit) is ExactPriceOrderCommandBatch
    assert not unit.base.order_commands
    row = _wire_row('trading_order_command_exact_price_v1', unit.command)
    restored = restore_exact_price_row('trading_order_command_v1', row, stored_utc=True)
    assert restored['aux_price'] == request.auxPrice
    assert restored['limit_price'] is None
    assert restored['quantity'] == '5.0000000000'
    assert restored['order_type'] == 'STP' and restored['side'] == 'SELL'
    assert restored['client_order_id'] == request.cOID
    families = dict(seal_exact_order_command_batch(unit))
    assert 'trading_order_command_v1' not in families
    assert families['trading_order_command_exact_price_v1'][0]['content_hash'] == unit.command['content_hash']


def test_exact_order_preserves_unknown_field_and_content_guards():
    request, scope = request_and_scope()
    for unsupported in (
        replace(request, raw={'unknown': 'evidence'}),
        replace(request, strategyParameters=({'tag': 'unknown'},)),
    ):
        with pytest.raises(ValueError, match='unmodeled'):
            order_command_batch(unsupported, **scope, exact_prices=True)
    unit = order_command_batch(request, **scope, exact_prices=True)
    with pytest.raises(ValueError, match='hash mismatch'):
        replace(unit, command={**unit.command, 'aux_price_bits': 1})
    with pytest.raises(ValueError, match='explicit boolean'):
        order_command_batch(request, **scope, exact_prices=1)
