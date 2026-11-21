import math
import torch
from torch.nn import functional as F
from ..phase0_v2.model import AttentionBlock
from ..phase1_v12.model import Autoencoder as Base


class StableAttention(AttentionBlock):
    def forward(self, x):
        n, t, d = x.shape
        # Projection, Q/K normalization, softmax and attention backward use FP32.
        # Fixed per-head norm bounds scores; no learned unbounded temperature.
        with torch.autocast(x.device.type, enabled=False):
            q, k, v = self.qkv(self.norm1(x.float())).reshape(
                n, t, 3, self.heads, d // self.heads
            ).permute(2, 0, 3, 1, 4).unbind(0)
            q = F.normalize(q, dim=-1, eps=1e-6) * math.sqrt(d // self.heads)
            k = F.normalize(k, dim=-1, eps=1e-6) * math.sqrt(d // self.heads)
            h = F.scaled_dot_product_attention(q, k, v, dropout_p=0.)
            h = h.transpose(1, 2).reshape(n, t, d)
            x = x.float() + self.projection(h)
        return x + self.ff(self.norm2(x))


class Autoencoder(Base):
    def __init__(self, config, mean, scale):
        super().__init__(config, mean, scale)
        for i in range(config.layers):
            old = self.encoder.blocks[i]
            new = StableAttention(config.width, config.heads)
            new.load_state_dict(old.state_dict())
            self.encoder.blocks[i] = new
