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
