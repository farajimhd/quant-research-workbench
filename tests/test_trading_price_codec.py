import math
import struct

import numpy as np
import pytest

from src.trading_runtime.trading_price_codec import encode_trading_price, decode_trading_price
from src.trading_runtime.strategy_one_broker_match_snapshot import float64_bits


@pytest.mark.parametrize("value", [0.0001, 0.8, 9.99, 50., 1000.,
    float(np.nextafter(np.float32(0), np.float32(1))),
    float(np.finfo(np.float32).max)])
def test_float32_price_round_trip_preserves_native_bits(value):
    number = float(np.float32(value))
    bits = encode_trading_price(number)
    assert bits == float64_bits(number, "price")
    restored = decode_trading_price(bits)
    assert struct.pack(">d", restored) == struct.pack(">d", number)
    assert np.float32(restored).tobytes() == np.float32(number).tobytes()


def test_price_domains_and_nullable_values_use_explicit_shared_conventions():
    assert encode_trading_price(None, nullable=True) is None
    assert decode_trading_price(None, nullable=True) is None
    assert encode_trading_price(-0., allow_zero=True) == 0
    assert decode_trading_price(0, allow_zero=True) == 0.
    assert decode_trading_price(encode_trading_price(9)) == 9.
    assert decode_trading_price(encode_trading_price(2 ** 53 + 2)) == 2 ** 53 + 2
    for value in (None, 0., -1., True, "9.99", math.nan, math.inf, 2 ** 53 + 1, 10 ** 1000):
        with pytest.raises(ValueError):
            encode_trading_price(value)
    for bits in (None, 0, True, "1", -1, 2 ** 64, 2 ** 63,
            0x7FF0000000000000, 0x7FF8000000000000, float64_bits(-1., "negative")):
        with pytest.raises(ValueError):
            decode_trading_price(bits)
    with pytest.raises(ValueError, match="booleans"):
        encode_trading_price(9., nullable=1)
    with pytest.raises(ValueError, match="booleans"):
        decode_trading_price(encode_trading_price(9.), allow_zero=1)
