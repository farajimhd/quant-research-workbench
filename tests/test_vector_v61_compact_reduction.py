import pytest
import torch
from research.vectorized_backtest.v61.feature_layout import column_sum
from research.vectorized_backtest.v61.features import CATALOG

@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA parity requires workstation')
def test_compact_reduction_exact_canonical_order():
    torch.manual_seed(171)
    for rows in (1,7,31,32,127,255,256,513,787,1024,2049,4095,4096):
        for clocks,columns in ((1,1),(1,17),(2,128),(32,694),(3,833)):
            values=torch.randn((clocks,rows,columns),device='cuda')
            values*=torch.logspace(-6,6,columns,device='cuda')[None,None]
            ids=torch.randperm(len(CATALOG),device='cuda')[:columns]
            oracle=torch.zeros((*values.shape[:-1],len(CATALOG)),device='cuda').index_copy(-1,ids,values).sum(-2).index_select(-1,ids)
            actual=column_sum(values,ids)
            torch.testing.assert_close(actual,oracle,rtol=0,atol=0,msg=f"rows={rows} clocks={clocks} columns={columns}")
