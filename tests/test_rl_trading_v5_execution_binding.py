import numpy as np
import polars as pl
import pytest

from research.rl_trading.v1.v5_execution_binding import project_certified_seconds


def _bars():
    # bucket_index 14_400 completes at second 1 after 04:00 ET.
    return pl.DataFrame(dict(bucket_index=[14_400, 14_401],
        open_int=[100_000, 110_000], close_int=[105_000, 115_000],
        price_valid=[1, 1], volume=[100., 0.], trade_count=[10, 0]))


def test_execution_uses_next_observed_open_and_excludes_zero_volume():
    grid = project_certified_seconds(_bars(), 9.)
    assert grid['close'][0] == 0.
    assert grid['close'][1] == 10.5
    assert grid['close'][2] == 11.5
    assert grid['next_open'][1] == 10.
    assert grid['next_open'][2] == 11.
    assert grid['fresh'][1] and not grid['fresh'][2]
    assert grid['volume'][2] == 0.
    assert grid['estimated_reference'][1] == 9.
    assert grid['prior_close'] == 9.


def test_execution_projection_rejects_invalid_prior_close():
    with pytest.raises(ValueError, match='Prior'):
        project_certified_seconds(_bars(), float('nan'))
