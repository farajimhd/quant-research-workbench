"""Feature-only queue ownership and bounded ordered dispatch."""
from concurrent.futures import ThreadPoolExecutor
from research.vectorized_backtest.v4.torch_backtest import extract_features as module


def test_dispatches_native_feature_worker_in_order_with_bounded_lookahead(monkeypatch):
    requested=[];called=[]
    def packets():
        for item in range(7):
            requested.append(item)
            yield item
    def features(packet):
        called.append(packet)
        return ('listing-'+str(packet),'features')
    monkeypatch.setattr(module,'work',features)
    with ThreadPoolExecutor(max_workers=2) as pool:
        iterator=module.ordered_features(pool,packets(),3)
        assert next(iterator)==('listing-0','features')
        assert requested==[0,1,2]
        rest=list(iterator)
    assert rest==[('listing-'+str(i),'features') for i in range(1,7)]
    assert sorted(called)==list(range(7))
