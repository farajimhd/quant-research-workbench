from types import SimpleNamespace
import pytest
import torch
from research.vectorized_backtest.v32.io_pipeline import TransferStaging


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
def test_completed_staging_release_preserves_device_values_and_reactivation():
    data=SimpleNamespace(host_tensors={'x':torch.arange(1024*1024,dtype=torch.float32)},
        resident_features=SimpleNamespace(host={'y':torch.arange(4096,dtype=torch.int64)},column_plans={}))
    with TransferStaging() as staging:
        for _ in range(2):
            staging.activate([('day',data)],'cuda')
            assert staging.release_completed()>0
            assert all(s['buffer'] is None and s['event'] is None for s in staging.slots)
            torch.testing.assert_close(data.tensors['x'].cpu(),data.host_tensors['x'],rtol=0,atol=0)
            torch.testing.assert_close(data.resident_features.tensors['y'].cpu(),data.resident_features.host['y'],rtol=0,atol=0)
