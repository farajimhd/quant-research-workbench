"""Framing and failure atomicity; these fixtures do not certify market data."""
from http.client import IncompleteRead
from io import BytesIO
from unittest.mock import patch

import pytest

from research.mlops.clickhouse import ClickHouseHttpClient
from src.backend.backtest_complete_market_response import (
    CompleteMarketResponseBounds, read_complete_market_response,
)


class Response(BytesIO):
    def __init__(self, body, *, length=None, fail=False):
        super().__init__(body)
        self.headers = {} if length is None else {'Content-Length': length}
        self.fail = fail

    def read(self, size=-1):
        if self.fail:
            raise IncompleteRead(b'{"boundary_ms":100}\n')
        return super().read(size)


def client():
    return ClickHouseHttpClient('http://localhost:8123', 'backtest_v3_reader',
                                'fixture-only', default_query_params={'readonly': 1})


def read(response, *, bounds=None, sql='SELECT 1 FORMAT JSONEachRow'):
    bounds = bounds or CompleteMarketResponseBounds(1024, 10, 65536)
    with patch('src.backend.backtest_complete_market_response.request.urlopen', return_value=response) as call:
        result = read_complete_market_response(client(), sql, bounds=bounds)
        assert call.call_count == 1
        assert response.closed
        return result


def test_complete_response_closes_before_any_rows_escape_and_preserves_order():
    body = b'{"boundary_ms":100,"levels":[[123,0.25]]}\n{"boundary_ms":200,"quote":null}\n'
    response = Response(body, length=str(len(body)))
    assert read(response) == ({'boundary_ms': 100, 'levels': [[123, .25]]},
                              {'boundary_ms': 200, 'quote': None})


def test_partial_http_response_returns_no_rows_and_is_not_retried():
    response = Response(b'', fail=True)
    with patch('src.backend.backtest_complete_market_response.request.urlopen', return_value=response) as call:
        with pytest.raises(IncompleteRead):
            read_complete_market_response(client(), 'SELECT 1 FORMAT JSONEachRow',
                                          bounds=CompleteMarketResponseBounds(1024, 10, 65536))
        assert call.call_count == 1
        assert response.closed


@pytest.mark.parametrize('body,length', [
    (b'{"a":1}\n', '20'), (b'{"a":1}', None),
    (b'{"a":1,"a":2}\n', None), (b'{"a":NaN}\n', None),
    (b'{"a":Infinity}\n', None), (b'[]\n', None),
    (b'{"a":1e999}\n', None),
    (b'{"a":1}\n__exception__\n', None),
])
def test_malformed_or_partial_body_cannot_release_its_valid_prefix(body, length):
    with pytest.raises(ValueError):
        read(Response(body, length=length))


@pytest.mark.parametrize('bounds', [
    CompleteMarketResponseBounds(4, 10, 65536),
    CompleteMarketResponseBounds(1024, 1, 65536),
    CompleteMarketResponseBounds(1024, 10, 20),
])
def test_budget_exhaustion_never_truncates(bounds):
    with pytest.raises(ValueError):
        read(Response(b'{"a":1}\n{"a":2}\n'), bounds=bounds)


@pytest.mark.parametrize('value', [True, 0, -1, 1.0, 16 * 1024 * 1024 + 1])
def test_wire_bounds_are_exact_and_bounded(value):
    with pytest.raises(ValueError):
        CompleteMarketResponseBounds(value, 10, 65536)


def test_write_or_unselected_client_is_rejected_before_http():
    with patch('src.backend.backtest_complete_market_response.request.urlopen') as call:
        with pytest.raises(ValueError):
            read_complete_market_response(client(), 'INSERT INTO x FORMAT JSONEachRow',
                                          bounds=CompleteMarketResponseBounds(1024, 10, 65536))
        bad = client()
        bad.default_query_params['readonly'] = '0'
        with pytest.raises(ValueError):
            read_complete_market_response(bad, 'SELECT 1 FORMAT JSONEachRow',
                                          bounds=CompleteMarketResponseBounds(1024, 10, 65536))
        call.assert_not_called()
