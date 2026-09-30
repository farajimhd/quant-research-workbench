import http.client

from research.mlops.clickhouse import _CoalescedHTTPConnection


def wire(connection_type, body, *, chunked=False):
    connection = connection_type('localhost')
    writes = []
    connection.send = writes.append
    connection.request('POST', '/?readonly=1', body=body,
                       headers={'Content-Type': 'text/plain'}, encode_chunked=chunked)
    return writes


def test_small_sql_wire_bytes_identical_in_single_write():
    body = 'SELECT \'café\''.encode('utf-8')
    reference = wire(http.client.HTTPConnection, body)
    combined = wire(_CoalescedHTTPConnection, body)
    assert len(reference) == 2
    assert len(combined) == 1
    assert b''.join(combined) == b''.join(reference)


def test_large_body_retains_standard_streaming_path():
    body = b'x' * 65537
    assert wire(_CoalescedHTTPConnection, body) == wire(http.client.HTTPConnection, body)


def test_chunked_iterable_retains_standard_framing():
    body = [b'first', b'second']
    assert wire(_CoalescedHTTPConnection, body, chunked=True) == wire(
        http.client.HTTPConnection, body, chunked=True)
