from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from scripts import build_market_day as builder


@pytest.mark.parametrize('readonly,expected',((False,None),(True,1)))
def test_readonly_is_per_client_http_setting_and_write_default_is_preserved(readonly,expected):
    path=Mock();path.is_file.return_value=False
    args=SimpleNamespace(env_file=path,max_threads=2,max_memory_gb=1,query_timeout=45)
    with patch.dict(builder.os.environ,{'QMD_CLICKHOUSE_URL':'http://example.invalid','PYTHONDONTWRITEBYTECODE':'1'},clear=True),\
         patch.object(builder,'ClickHouseHttpClient') as http:
        client=builder.Client(args,persistent=False,readonly=readonly)
        settings=http.call_args_list[0].kwargs['default_query_params']
        assert settings.get('readonly')==expected
        assert settings['max_threads']==2 and settings['max_memory_usage']==1024**3
        client.close()


def test_reuses_main_and_independent_cancellation_sockets_with_idle_expiry():
    path=Mock();path.is_file.return_value=False
    args=SimpleNamespace(env_file=path,max_threads=2,max_memory_gb=1,query_timeout=45)
    with patch.dict(builder.os.environ,{'QMD_CLICKHOUSE_URL':'http://example.invalid'},clear=True),\
         patch.object(builder,'ClickHouseHttpClient') as http:
        client=builder.Client(args,persistent=True)
        assert all(c.kwargs['persistent'] for c in http.call_args_list)
        assert all(c.kwargs['max_persistent_idle_seconds']==2 for c in http.call_args_list)
        client.close()


def test_failed_cancellation_preserves_original_transport_failure():
    client=object.__new__(builder.Client)
    client.http=Mock();client.http.execute.side_effect=OSError(10048,'socket exhausted')
    client.cancel_http=Mock();client.cancel_http.execute.side_effect=TimeoutError('cancel unavailable')
    client.profile_totals={};client.profiles=[];client.active=None
    with pytest.raises(OSError,match='socket exhausted') as caught:
        client.query('SELECT 1')
    assert 'Cancellation could not be confirmed' in caught.value.__notes__[0]
    assert client.active is None
