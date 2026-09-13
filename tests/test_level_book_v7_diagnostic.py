import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.diagnose_level_book_v7_urg import capture_fits, json_safe
from src.market_engine import reaction_band, reaction_center, streaming_level_book


def test_instrumentation_preserves_real_mle_results_and_restores_functions():
    observations = [dict(price=p, resolution=.01, at=i, resolved_at=i+1)
                    for i, p in enumerate([4.01, 4.02, 4.015, 4.005])]
    reaction_band.cached_fit.cache_clear()
    expected = streaming_level_book.partition(observations, .8)
    reaction_band.cached_fit.cache_clear()
    originals = (streaming_level_book.partition, reaction_band.fit, reaction_center.minimize)
    with capture_fits(streaming_level_book, reaction_band, reaction_center) as state:
        actual = streaming_level_book.partition(observations, .8)
        assert actual == expected
        assert state['optimizer_calls'] > 0
        assert state['last_partition']['component_fits'][0]['trials']
    assert originals == (streaming_level_book.partition, reaction_band.fit, reaction_center.minimize)


def test_failed_optimizer_evidence_and_exception_cleanup():
    def minimize(*args, **kwargs):
        return SimpleNamespace(success=False, message='ABNORMAL', fun=float('nan'), x=np.array([1., 2.]))

    center = SimpleNamespace(minimize=minimize)

    def fit(prices, resolution):
        center.minimize(None, [0., 1.], method='L-BFGS-B')
        return dict(status='fit_failed')

    band = SimpleNamespace(fit=fit)
    streaming = SimpleNamespace(partition=lambda obs, coverage: [(obs, band.fit([o['price'] for o in obs], .01))])
    originals = (streaming.partition, band.fit, center.minimize)
    with pytest.raises(RuntimeError):
        with capture_fits(streaming, band, center) as state:
            streaming.partition([dict(price=4., resolution=.01)], .8)
            evidence = state['last_partition']['component_fits'][0]
            assert evidence['result']['status'] == 'fit_failed'
            assert evidence['trials'][0]['message'] == 'ABNORMAL'
            json.dumps(json_safe(evidence), allow_nan=False)
            raise RuntimeError('replay stopped')
    assert originals == (streaming.partition, band.fit, center.minimize)
