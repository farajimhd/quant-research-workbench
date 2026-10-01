"""Only transient transport errors permit restarting read-only source units."""
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from pipelines.strategy_one.source_read_retry import restartable_read_error


@pytest.mark.parametrize("error", [IncompleteRead(b"", 1),
                                  URLError(ConnectionRefusedError()),
                                  URLError(TimeoutError())])
def test_transport_errors_restart(error):
    assert restartable_read_error(error)


@pytest.mark.parametrize("error", [HTTPError("url", 500, "SQL", {}, None),
                                  URLError("certificate verification failed"),
                                  ValueError("invalid source")])
def test_authority_and_data_errors_remain_strict(error):
    assert not restartable_read_error(error)
