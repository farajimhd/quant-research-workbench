"""Carry a fixed cash acquisition request through native limit execution."""
from decimal import Decimal, ROUND_FLOOR


def cash_bounded_buy_price(intent, price, *, quantity=None, tick_size=None):
    """Bound purchase notional, excluding separately accounted commissions.

    Keep the original approved quantity throughout partial fills/reprices: using
    the remaining quantity would incorrectly spend the whole budget again.
    This does not change fixed-quantity, relative sizing or sell instructions.
    """
    request = intent.capital_request
    if (intent.action not in {'enter_long', 'add_long'} or request is None
            or request.mode != 'fixed_notional'):
        return price
    size = Decimal(str(intent.quantity if quantity is None else quantity))
    cash = Decimal(str(request.value))
    if not size.is_finite() or size <= 0 or not cash.is_finite() or cash <= 0:
        raise ValueError('Fixed cash execution requires positive approved quantity and cash')
    ceiling = cash / size
    if Decimal(str(price)) <= ceiling:
        return price
    if tick_size is not None:
        tick = Decimal(str(tick_size))
        if not tick.is_finite() or tick <= 0:
            raise ValueError('Fixed cash execution requires a positive tick size')
        ceiling = (ceiling / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if ceiling <= 0:
        raise ValueError('Fixed cash execution cannot fund one price tick')
    return float(ceiling)
