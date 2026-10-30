import unittest
from types import SimpleNamespace
import numpy as np
import torch
from research.vectorized_backtest.v59.swing_projection import prepare
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS

class SwingProjectionTests(unittest.TestCase):
    def test_missing_trade_bar_does_not_erase_available_history(self):
        width=len(SWING_WINDOWS)**2
        values=np.full((3,211+width),np.nan);values[1,211:]=10.;values[2,211:]=20.
        data=SimpleNamespace(host_tensors={'membership':torch.tensor([[True],[True],[True],[True]]),'observed':torch.zeros((4,1),dtype=torch.bool)},bank={'ids':np.array([[0],[1],[0],[2]]),'values':values})
        result=prepare(data);ids=result['v59_entry_swing_ids'];bank=result['v59_entry_swing_values']
        self.assertEqual(bank[ids[1,0],0],10.)
        self.assertTrue(np.isnan(bank[ids[2,0],0]))
        self.assertEqual(ids[-1,0],0)
        self.assertTrue(np.isnan(bank[0]).all())
