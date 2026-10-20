"""One bounded scan of normalized rows; no cached certificate or write replay."""
from contextlib import closing
from uuid import uuid4


def normalized_stream(http, sql, *, expected):
    """Keep server memory/thread caps and bound total rows and response bytes.

    Unlike LIMIT/cursor pagination this executes FINAL only once. The caller
    must consume the iterator completely and compare the expected count. An
    extra row is deliberately requested so excess data cannot be truncated.
    """
    if type(expected) is not int or not 0 <= expected <= 1_000_000:
        raise ValueError('Normalized stream expected count is outside its bound')
    if not sql.startswith('SELECT ') or ' FINAL ' not in sql:
        raise ValueError('Normalized stream requires a FINAL SELECT')
    limits = dict(max_result_rows=expected + 1,
                  max_result_bytes=min(512 * 1024**2,
                                       max(16_000_000, (expected + 1) * 2048)),
                  result_overflow_mode='throw', max_block_size=5000)
    with closing(http.iter_json_each_row(
            sql + f' LIMIT {expected + 1} FORMAT JSONEachRow',
            query_id='structural-normalized-' + uuid4().hex,
            query_params=limits)) as rows:
        count = 0
        for row in rows:
            if count >= expected:
                raise ValueError('Normalized checkpoint snapshot differs: extra rows')
            count += 1
            yield row
        if count != expected:
            raise ValueError('Normalized checkpoint snapshot differs: missing rows')
