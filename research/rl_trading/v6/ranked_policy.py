"""V6 attention actor-critic with stable full-market action identity axes."""
import torch
from research.rl_trading.v6.actor_critic import BracketActorCritic
from research.rl_trading.v6.market_attention import (
    MarketAttentionConfig, RankedMarketAttention, VolumeRanker)


class RankedBracketActorCritic(BracketActorCritic):
    def __init__(self, width=128, *, config=None):
        super().__init__(width)
        self.ranking_config = config or MarketAttentionConfig()
        self.market_attention = RankedMarketAttention(width, self.ranking_config)
        self.ranker = None
        self.candle_state = None
        self.clock_us = None
        self.pending_indices = ()
        self._critic_market = None

    def reset_market(self, listings):
        """Reset once per session; never reset when ranks change."""
        self.ranker = VolumeRanker(listings, self.ranking_config)
        self.candle_state = None
        self.clock_us = None
        self.pending_indices = ()
        self._critic_market = None

    def observe_market(self, state, close_us, indices, scalar):
        """Bind chronological state, consume existing V6 scalar volume once."""
        if self.ranker is None or len(self.ranker.seen) != len(state.encoded):
            raise ValueError('Market must be reset to certified session population')
        self.ranker.observe(close_us, indices, scalar)
        self.candle_state, self.clock_us = state, close_us

    def set_pending(self, indices):
        self.pending_indices = tuple(indices)

    def decide(self, listing_embeddings, account, held_index, held_features,
               action_state, **masks):
        if self.candle_state is None:
            raise ValueError('Attention requires observed V6 candle state')
        selected_np = self.ranker.select(self.clock_us,
            held=held_index.detach().cpu().tolist(), pending=self.pending_indices)
        selected = torch.as_tensor(selected_np, device=listing_embeddings.device, dtype=torch.long)
        enriched = self.market_attention(self.candle_state.history,
            self.candle_state.seen, listing_embeddings, selected)
        # Keep [N,D] and the original token IDs. Sorting never rekeys holdings.
        full = listing_embeddings.index_copy(0, selected, enriched)
        self._critic_market = full
        selected_mask = torch.zeros(len(full), device=full.device, dtype=torch.bool)
        selected_mask[selected] = True
        masks = dict(masks)
        masks['enter_allowed'] = masks['enter_allowed'] & selected_mask
        return super().decide(full, account, held_index, held_features, action_state, **masks)

    def critic_market_embeddings(self, listings):
        if self._critic_market is None:
            raise ValueError('Critic requires same-decision causal attention context')
        return self._critic_market
