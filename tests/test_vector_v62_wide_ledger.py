import os
from types import SimpleNamespace
import pytest
import torch


@pytest.mark.skipif(os.environ.get('V60_TEST_CUDA')!='1',reason='opt-in GPU')
def test_order_scatter_and_pack_use_64_bit_row_offsets(tmp_path):
    from research.vectorized_backtest.v62.compiler_runtime import configure_caches
    configure_caches(tmp_path/'compiler')
    from research.vectorized_backtest.v62.order_scatter import append_pair
    from research.vectorized_backtest.v62.ledger_pack import pack
    if torch.cuda.mem_get_info()[0]<24*2**30:pytest.skip('Wide-address fixture requires 24 GiB free')
    # Only six records are touched. The last row starts beyond signed int32,
    # exercising the actual kernels without replaying a large population.
    data=torch.empty_strided((3,4,9),((1<<30)+27,9,1),device='cuda',dtype=torch.float64)
    counts=torch.zeros(3,device='cuda',dtype=torch.int64)
    records=torch.zeros((1,3,2,17),device='cuda',dtype=torch.float64)
    records[...,:8]=torch.arange(48,device='cuda').reshape(1,3,2,8)
    complete=torch.ones((1,3,2),device='cuda',dtype=torch.bool)
    append_pair(SimpleNamespace(counts=counts,data=data,u=2,capacity=2),complete,records)
    assert counts.cpu().tolist()==[2,2,2]
    actual=pack([data[...,i] for i in range(9)],counts,counts.cpu().numpy()).cpu()
    expected=torch.cat((torch.arange(2).repeat(3).reshape(-1,1),torch.arange(48).reshape(6,8)),1).double()
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
