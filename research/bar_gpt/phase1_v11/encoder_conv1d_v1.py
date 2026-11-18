"""Versioned temporal convolution ablation; decoder receives only the exported vector."""
import torch
from torch import nn


class TemporalConvEncoder(nn.Module):
    def __init__(self, width, embedding, layers):
        super().__init__()
        if layers != 6 or embedding <= 32:
            raise ValueError('Conv1d v1 requires six downsampling stages and embedding >32')
        blocks = []
        incoming = 73
        for index in range(6):
            outgoing = width if index < 2 else 2 * width
            blocks.extend([
                nn.Conv1d(incoming, incoming, 5, stride=2, padding=2, groups=incoming),
                nn.Conv1d(incoming, outgoing, 1),
                nn.GroupNorm(1, outgoing), nn.GELU(),
            ])
            incoming = outgoing
        self.trunk = nn.Sequential(*blocks)
        self.readout = nn.Conv1d(incoming, embedding - 32, 1)
        self.scale_projection = nn.Conv1d(18, 32, 1)

    def forward(self, slots, references, precision):
        x = torch.cat((slots.transpose(1, 2), references.unsqueeze(-1).expand(-1, -1, 64)), 1)
        with torch.autocast(x.device.type, dtype=torch.bfloat16,
                            enabled=x.is_cuda and precision == 'bf16'):
            h = self.trunk(x)
        return torch.cat((self.readout(h.float()).squeeze(-1),
                          self.scale_projection(references.float().unsqueeze(-1)).squeeze(-1)), -1)
