"""Current producer transport bounds, lazy authority and ambiguous-write safety."""
import pytest

from pipelines.market_sip.events.current_market_day_transport import (
    MarketDayQueryLimits, current_market_day_sql_factory,
)


class Http:
    def __init__(self, response='{"n":1}\n', error=None):
        self.response, self.error = response, error
        self.calls = []
        self.closes = 0

    def execute(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closes += 1


def test_lazy_settings_named_rows_and_owned_close():
    http, settings = Http(), []
    def create(value):
        settings.append(value)
        return http
    factory = current_market_day_sql_factory(create, MarketDayQueryLimits(2, 1024, 30, 2, 64))
    assert not settings and not http.calls
    client = factory()
    assert settings[0]['max_bytes_before_external_group_by'] == 0
    assert settings[0]['max_bytes_before_external_sort'] == 0
    assert settings[0]['result_overflow_mode'] == 'throw'
    assert client.query('SELECT count() AS n') == [{'n': 1}]
    assert http.calls[0][0].endswith(' FORMAT JSONEachRow')
    assert http.calls[0][1]['query_id'].startswith('current-market-day-')
    client.close()
    client.close()
    assert http.closes == 1
    with pytest.raises(RuntimeError, match='closed'):
        client.query('SELECT 1')


@pytest.mark.parametrize('response,rows,byte_limit', [('{"n":1}\n' * 3, 2, 64),
    ('{"n":123456789}\n', 2, 8), ('[1]\n', 2, 64)])
def test_response_bounds_and_named_contract(response, rows, byte_limit):
    client = current_market_day_sql_factory(lambda _: Http(response),
        MarketDayQueryLimits(2, 1024, 30, rows, byte_limit))()
    with pytest.raises(RuntimeError):
        client.query('SELECT 1')
    client.close()


def test_uncertain_insert_is_never_retried():
    http = Http(error=TimeoutError('uncertain acknowledgment'))
    client = current_market_day_sql_factory(lambda _: http,
        MarketDayQueryLimits(2, 1024, 30, 2, 64))()
    with pytest.raises(TimeoutError):
        client.query('INSERT INTO arte.bars_v1 SELECT 1', read=False)
    assert len(http.calls) == 1
    assert not http.calls[0][0].endswith('FORMAT JSONEachRow')
    client.close()


@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_limits_are_explicit_and_positive(value):
    with pytest.raises(ValueError):
        MarketDayQueryLimits(value, 1024, 30, 2, 64)
