"""Joint intra/inter-frame attention with a small latent-only readout."""
import torch
from torch import nn
from torch.nn import functional as F
from .config import Config, FRAME_IDS, METADATA, POSITIONS
from ..v5.encoder_v3 import AttentionBlock


class LatentEncoder(nn.Module):
    def __init__(self, config=Config()):
        super().__init__()
        self.config = config
        self.frame = nn.Embedding(5, 512)
        self.position = nn.Embedding(8, 512)
        self.metadata = nn.Linear(METADATA, 512)
        self.coordinates = nn.Linear(512, 512)
        self.summary = nn.Parameter(torch.empty(1, 1, 512))
        nn.init.normal_(self.summary, std=.02)
        self.register_buffer('frames', torch.tensor(FRAME_IDS), persistent=False)
        self.register_buffer('positions', torch.tensor(POSITIONS), persistent=False)
        self.layers = nn.ModuleList([
            AttentionBlock(512,config.heads,config.feedforward,config.dropout,'sdpa',config.dropout)
            if config.attention_layout=='batch-first-sdpa' else
            nn.TransformerEncoderLayer(512,config.heads,config.feedforward,config.dropout,
                                       activation='gelu',batch_first=True,norm_first=True)
            for _ in range(config.layers)])
        self.norm = nn.LayerNorm(512)

    def forward(self, tokens, metadata, feature_ids=None):
        if feature_ids is None:
            feature_ids=torch.arange(512,device=tokens.device)
        if tokens.ndim != 3 or tokens.shape[1] != 16 or tokens.shape[2] != len(feature_ids):
            raise ValueError('Expected [batch,16,retained_coordinates]')
        if (feature_ids.ndim != 1 or feature_ids.dtype != torch.int64
                or not 1 <= len(feature_ids) <= 512):
            raise ValueError('Retained coordinate IDs must be a nonempty int64 vector')
        if metadata.shape != (*tokens.shape[:2], METADATA):
            raise ValueError('Expected nine causal metadata values per token')
        # Only retained input columns and the matching learned projection weights
        # enter the encoder. No zero placeholders or learned mask tokens.
        hidden = F.linear(tokens, self.coordinates.weight.index_select(1,feature_ids),
                          self.coordinates.bias)
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

    def _coordinate_draw(self,device):
        expected=512*self.config.mask_rate
        count=int(expected)+int(torch.rand(()) < expected%1)
        return torch.randperm(512,device=device),count

    def draw_feature_ids(self,device):
        """Same random subset/order, without a GPU nonzero/count readback."""
        if not self.config.mask_rate:return torch.arange(512,device=device)
        permutation,count=self._coordinate_draw(device)
        return permutation[count:].sort().values

    def draw_mask(self, device):
        """Fresh shared coordinates each batch; expected102.4 of512 at default20%."""
        if not self.config.mask_rate:return torch.zeros(512,dtype=torch.bool,device=device)
        permutation,count=self._coordinate_draw(device)
        mask=torch.zeros(512,dtype=torch.bool,device=device)
        mask[permutation[:count]]=True
        return mask

    def forward(self, tokens, metadata, mask=None):
        if tokens.ndim != 3 or tokens.shape[1:] != (16,512):
            raise ValueError('Expected [batch,16,512] historical embeddings')
        if mask is None:
            mask=self.draw_mask(tokens.device) if self.training else torch.zeros(
                512,dtype=torch.bool,device=tokens.device)
        if mask.shape != (512,) or mask.dtype != torch.bool:
            raise ValueError('Coordinate mask must be bool[512], shared by the batch')
        ids=(~mask).nonzero().flatten()
        state = self.encoder(tokens.index_select(2,ids), metadata, ids)
        return state, self.predictor(state)

    @torch.no_grad()
    def encode_context(self,tokens,metadata):
        """Production/phase-three extraction always uses every latent coordinate."""
        if self.training:raise ValueError('Production embedding extraction requires eval mode')
        return self.encoder(tokens,metadata)


def future_loss(predicted, target):
    if predicted.shape != target.shape or predicted.ndim != 2 or predicted.shape[1] != 512:
        raise ValueError('Future prediction and target must be [batch,512]')
    return (predicted.float() - target.detach().float()).square().mean()
