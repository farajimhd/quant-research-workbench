from types import SimpleNamespace

import numpy as np
import torch

from research.rl_trading.v6 import prepare_feedback_benchmark as preparation


def test_prefix_enrichment_only_uses_available_contiguous_clocks(monkeypatch):
    start = preparation.origin('2026-07-31')
    keys = [('2026-07-31', start + 3_000_000, '2026-07-31:A:pair:2', 0, False)]
    seen = []
    def prefix(session, identity, targets, held, session_start):
        clocks, available, reset = preparation.clock_grid(targets, session_start)
        return dict(raw=torch.zeros(1, 9, 120, 147), present=torch.zeros(1, 9, 120, dtype=torch.bool),
            held=torch.zeros(1, 9, 11), clocks_us=torch.from_numpy(clocks),
            available=torch.from_numpy(available), reset=torch.from_numpy(reset),
            max_input_close_us=torch.full((1, 9), -1, dtype=torch.int64))
    def enrich(session, requested, names, quotes):
        seen.extend(k[1] for k in requested)
        return dict(liquidity=torch.tensor([[float(k[1]-start)] for k in requested]))
    monkeypatch.setattr(preparation, 'prefix_windows', prefix)
    monkeypatch.setattr(preparation, 'prepare_day', enrich)
    result = preparation.prepare_prefix_day(SimpleNamespace(day='2026-07-31'), keys,
        torch.zeros(1, 11), {'A':'A'}, None)
    assert seen == list(start + np.arange(4) * 1_000_000)
    assert result['liquidity'].shape == (1, 9, 1)
    assert result['liquidity'][0, -1, 0] == 3_000_000
    assert not result['available'][0, :5].any()
