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


def test_arrow_connection_timeout_retries_once_before_consumption(monkeypatch):
    from urllib.error import URLError
    import research.mlops.clickhouse as transport
    calls=[]
    def open_request(req,*,timeout):
        calls.append(req)
        if len(calls)==1:raise URLError(TimeoutError(10060,'connect timeout'))
        return _response()
    monkeypatch.setattr(transport.request,'urlopen',open_request)
    client=ClickHouseHttpClient('http://localhost:8123','reader','secret')
    batches=list(client.iter_arrow_record_batches('SELECT boundary_ms FORMAT ArrowStream',query_id='pinned-read'))
    assert [v for batch in batches for v in batch.column(0).to_pylist()]==[100,200,300]
    assert len(calls)==2 and calls[0] is calls[1]
    assert client.arrow_connect_retries==1


@pytest.mark.parametrize('failure',[TimeoutError(10060,'connect timeout'),PermissionError(13,'denied')])
def test_arrow_connection_retry_is_bounded_and_excludes_nontransient_errors(monkeypatch,failure):
    from urllib.error import URLError
    import research.mlops.clickhouse as transport
    calls=[]
    def open_request(req,*,timeout):calls.append(req);raise URLError(failure)
    monkeypatch.setattr(transport.request,'urlopen',open_request)
    client=ClickHouseHttpClient('http://localhost:8123','reader','secret')
    with pytest.raises(URLError):list(client.iter_arrow_record_batches('SELECT 1 FORMAT ArrowStream'))
    assert len(calls)==(2 if isinstance(failure,TimeoutError) else 1)


def test_arrow_partial_stream_is_never_reopened(monkeypatch):
    import research.mlops.clickhouse as transport
    calls=[]
    class Broken:
        def __iter__(self):
            yield pa.record_batch([[1]],names=['x'])
            raise TimeoutError('partial stream')
        def close(self):pass
    monkeypatch.setattr(transport.request,'urlopen',lambda *args,**kwargs:(calls.append(1) or _response()))
    monkeypatch.setattr(pa.ipc,'open_stream',lambda response:Broken())
    client=ClickHouseHttpClient('http://localhost:8123','reader','secret')
    stream=client.iter_arrow_record_batches('SELECT 1 FORMAT ArrowStream')
    assert next(stream).column(0).to_pylist()==[1]
    with pytest.raises(TimeoutError,match='partial stream'):next(stream)
    assert len(calls)==1 and client.arrow_connect_retries==0


def test_arrow_http_errors_and_nonselect_are_never_retried(monkeypatch):
    from urllib.error import HTTPError,URLError
    import research.mlops.clickhouse as transport
    calls=[]
    def denied(req,*,timeout):calls.append(req);raise HTTPError(req.full_url,401,'Unauthorized',{},BytesIO(b'denied'))
    monkeypatch.setattr(transport.request,'urlopen',denied)
    client=ClickHouseHttpClient('http://localhost:8123','reader','secret')
    with pytest.raises(RuntimeError,match='401'):list(client.iter_arrow_record_batches('SELECT 1 FORMAT ArrowStream'))
    assert len(calls)==1
    def timeout(req,*,timeout):calls.append(req);raise URLError(TimeoutError('connect'))
    monkeypatch.setattr(transport.request,'urlopen',timeout)
    with pytest.raises(URLError):list(client.iter_arrow_record_batches('INSERT INTO arte.x FORMAT ArrowStream'))
    assert len(calls)==2 and client.arrow_connect_retries==0


def test_structural_cancellation_failure_does_not_mask_read_error():
    from types import SimpleNamespace
    from urllib.error import URLError
    from pipelines.market_sip.events.structural_session_checkpoints import source_rows
    original=URLError(TimeoutError(10060,'original connect timeout'))
    def batches(*args,**kwargs):raise original;yield
    def cancel():raise RuntimeError('cancel unavailable')
    client=SimpleNamespace(http=SimpleNamespace(iter_arrow_record_batches=batches),cancel=cancel,profile_totals={},active=None)
    unit=SimpleNamespace(build_id='f'*64,session_date='2026-06-08',ticker='CSQ',attempt_id='59e8174d-4c22-4e8e-b779-7ff98331ac99')
    with pytest.raises(URLError) as caught:list(source_rows(client,unit))
    assert caught.value is original and client.active is None
    assert 'cancellation could not be confirmed' in original.__notes__[0]
