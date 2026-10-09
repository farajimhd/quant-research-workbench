from types import SimpleNamespace
from research.vectorized_backtest.v6.torch_backtest.sparse_structure import reference_transport


def test_reference_principal_binds_verified_endpoint_and_server_readonly(monkeypatch):
    from research.rl_trading.v1 import arte_source
    from src.backend import backtest_market_data
    from research.mlops import clickhouse
    closed=[];received={}
    previous=SimpleNamespace(user='test-reference-principal',password='test-placeholder',close=lambda:closed.append('previous'))
    reference=SimpleNamespace(_client=previous)
    transport=SimpleNamespace(base_url='http://verified.test:8123',close=lambda:closed.append('transport'))
    monkeypatch.setattr(arte_source,'reader',lambda **k:reference)
    monkeypatch.setattr(backtest_market_data,'readonly_clickhouse_client',lambda **k:transport)
    def client(url,user,password,**kwargs):received.update(url=url,user=user,password=password,**kwargs);return object()
    monkeypatch.setattr(clickhouse,'ClickHouseHttpClient',client)
    assert reference_transport() is reference
    assert received['url']==transport.base_url and received['user']==previous.user
    assert received['default_query_params']['readonly']==1
    assert closed==['previous','transport']
