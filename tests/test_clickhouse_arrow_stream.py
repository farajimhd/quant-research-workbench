"""Bounded ClickHouse Arrow transport for columnar Backtest reads."""
from io import BytesIO

import pyarrow as pa
import pytest

from research.mlops.clickhouse import ClickHouseHttpClient


def _response() -> BytesIO:
    output = pa.BufferOutputStream()
    schema = pa.schema([("boundary_ms", pa.int64()), ("close_int", pa.uint64())])
    with pa.ipc.new_stream(output, schema) as writer:
        writer.write_batch(pa.record_batch([[100, 200], [100_000, 100_100]],
                                           schema=schema))
        writer.write_batch(pa.record_batch([[300], [100_200]], schema=schema))
    return BytesIO(output.getvalue().to_pybytes())


def test_arrow_batches_stream_without_json_rows(monkeypatch):
    import research.mlops.clickhouse as transport
    opened = []
    def fake_urlopen(req, *, timeout):
        assert req.data == b"SELECT boundary_ms,close_int FORMAT ArrowStream"
        assert timeout == 5
        response = _response()
        opened.append(response)
        return response
    monkeypatch.setattr(transport.request, "urlopen", fake_urlopen)
    client = ClickHouseHttpClient("http://localhost:8123", "reader", "secret",
                                  timeout_seconds=5,
                                  default_query_params={"readonly": 1})
    stream = client.iter_arrow_record_batches(
        "SELECT boundary_ms,close_int FORMAT ArrowStream")
    assert next(stream).column(0).to_pylist() == [100, 200]
    assert not opened[0].closed
    stream.close()
    assert opened[0].closed


def test_arrow_transport_rejects_other_formats_before_network(monkeypatch):
    import research.mlops.clickhouse as transport
    monkeypatch.setattr(transport.request, "urlopen",
                        lambda *_args, **_kwargs: pytest.fail("unexpected HTTP"))
    client = ClickHouseHttpClient("http://localhost:8123", "reader", "secret")
    with pytest.raises(ValueError, match="FORMAT ArrowStream"):
        list(client.iter_arrow_record_batches("SELECT 1 FORMAT JSONEachRow"))
