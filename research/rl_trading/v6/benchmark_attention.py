"""Bounded synthetic GPU capacity benchmark; not a training/profit audit.

Run with PYTHONDONTWRITEBYTECODE=1. Prints metrics; saves no market data.
"""
import argparse
import json
import time
import torch
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.candle_stream import SparseCandleState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--listings', type=int, default=6200)
    parser.add_argument('--ranks', type=int, nargs='+', default=[100, 500])
    parser.add_argument('--iterations', type=int, default=10)
    args = parser.parse_args()
    if args.listings < 1 or args.iterations < 1:
        parser.error('Positive listings and iterations required')
    device = torch.device(args.device)
    for rank in args.ranks:
        torch.manual_seed(17)
        model = RankedBracketActorCritic(config=MarketAttentionConfig(top_r=rank)).to(device)
        state = SparseCandleState.empty(model.encoder, args.listings, device=device, dtype=torch.float32)
        state.history.normal_()
        state.encoded.normal_()
        state.seen.fill_(120)
        model.reset_market(args.listings)
        import numpy as np
        scalar = np.zeros((args.listings, 37), dtype=np.float32)
        scalar[:, 8] = np.log1p(np.arange(args.listings))
        model.observe_market(state, 1_000_000, np.arange(args.listings), scalar)
        action_state = model.initial_action_state(device=device, dtype=torch.float32)
        account = torch.tensor([10000.,10000.,0.,0.,0.,0.,0.],device=device)
        held = torch.arange(4, device=device)
        features = torch.zeros(4,9,device=device)
        masks = dict(enter_allowed=torch.ones(args.listings,device=device,dtype=torch.bool),
            exit_allowed=torch.ones(4,device=device,dtype=torch.bool),
            stop_allowed=torch.ones(4,device=device,dtype=torch.bool),
            target_allowed=torch.ones(4,device=device,dtype=torch.bool))
        def step():
            model.zero_grad(set_to_none=True)
            dist, value = model.distribution_and_value(state.encoded, account, held,
                                                       features, action_state, **masks)
            loss = -dist.log_prob(args.listings, torch.tensor(.1,device=device))+value.square()
            loss.backward()
        for _ in range(2):
            step()
        if device.type == 'cuda':
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        for _ in range(args.iterations):
            step()
        if device.type == 'cuda':
            torch.cuda.synchronize()
        print(json.dumps(dict(scope='synthetic_attention_forward_backward_only',
            device=str(device), gpu=torch.cuda.get_device_name() if device.type=='cuda' else None,
            listings=args.listings, top_r=rank, width=128, context_candles=120,
            iterations=args.iterations,
            milliseconds_per_step=1000*(time.perf_counter()-start)/args.iterations,
            peak_allocated_gib=torch.cuda.max_memory_allocated()/2**30 if device.type=='cuda' else None)))
        del model, state
        if device.type == 'cuda':
            torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
