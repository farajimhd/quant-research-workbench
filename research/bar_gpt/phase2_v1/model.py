"""Joint intra/inter-frame attention with a small latent-only readout."""
import torch
from torch import nn
from .config import Config, FRAME_IDS, METADATA, POSITIONS


class LatentEncoder(nn.Module):
    def __init__(self, config=Config()):
        super().__init__()
        self.config = config
        self.frame = nn.Embedding(5, 512)
        self.position = nn.Embedding(8, 512)
        self.metadata = nn.Linear(METADATA, 512)
        self.summary = nn.Parameter(torch.empty(1, 1, 512))
        self.mask = nn.Parameter(torch.empty(1, 1, 512))
        nn.init.normal_(self.summary, std=.02)
        nn.init.normal_(self.mask, std=.02)
        self.register_buffer('frames', torch.tensor(FRAME_IDS), persistent=False)
        self.register_buffer('positions', torch.tensor(POSITIONS), persistent=False)
        self.layers = nn.ModuleList([
            nn.TransformerEncoderLayer(512, config.heads, config.feedforward,
                                       config.dropout, activation='gelu',
                                       batch_first=True, norm_first=True)
            for _ in range(config.layers)])
        self.norm = nn.LayerNorm(512)

    def draw_mask(self, batch, device):
        """Exactly three/four whole tokens per sample at the default .2 rate."""
        expected = 16 * self.config.mask_rate
        count = int(expected) + (torch.rand(batch, device=device) < expected % 1).long()
        ranks = torch.rand(batch, 16, device=device).argsort(1).argsort(1)
        return ranks < count[:, None]

    def forward(self, tokens, metadata, mask=None):
        if tokens.ndim != 3 or tokens.shape[1:] != (16, 512):
            raise ValueError('Expected [batch,16,512] historical embeddings')
        if metadata.shape != (*tokens.shape[:2], METADATA):
            raise ValueError('Expected nine causal metadata values per token')
        if mask is None:
            mask = self.draw_mask(len(tokens), tokens.device) if self.training else torch.zeros(
                tokens.shape[:2], dtype=torch.bool, device=tokens.device)
        if mask.shape != tokens.shape[:2] or mask.dtype != torch.bool:
            raise ValueError('Mask must be bool [batch,16]')
        hidden = torch.where(mask[..., None], self.mask, tokens)
        hidden = hidden + self.frame(self.frames) + self.position(self.positions)
        hidden = hidden + self.metadata(metadata)
        hidden = torch.cat((self.summary.expand(len(tokens), -1, -1), hidden), 1)
        for layer in self.layers:
            hidden = layer(hidden)
        return self.norm(hidden[:, 0])


class Forecaster(nn.Module):
    def __init__(self, config=Config()):
        super().__init__()
        self.config = config
        self.encoder = LatentEncoder(config)
        self.predictor = nn.Sequential(nn.Linear(512, config.predictor_width),
                                       nn.GELU(), nn.Linear(config.predictor_width, 512))

    def forward(self, tokens, metadata, mask=None):
        state = self.encoder(tokens, metadata, mask)
        return state, self.predictor(state)


def future_loss(predicted, target):
    if predicted.shape != target.shape or predicted.ndim != 2 or predicted.shape[1] != 512:
        raise ValueError('Future prediction and target must be [batch,512]')
    return (predicted.float() - target.detach().float()).square().mean()
