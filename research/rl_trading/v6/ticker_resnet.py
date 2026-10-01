"""Local four-action ResNet control for the V6 classification diagnosis.

Uses the same causal 120-candle inputs, normalization, account/holding/cost
channels and ticker heads. It intentionally removes market attention and
recurrent execution memory: results cannot isolate those two components.
This diagnostic model is not a portfolio actor or a PPO checkpoint.
"""
import torch
from torch import nn
from research.rl_trading.v6.model import INPUT_WIDTH
from research.rl_trading.v6.resnet_baseline import ResidualBlock
from research.rl_trading.v6.execution_features import EXECUTION_SCALE
from research.rl_trading.v6.ticker_heads import TickerHeads

VERSION = 'rl-v6-local-four-action-resnet-control-v1'


class TickerResNet(nn.Module):
    """Independent [B,120,147] normalized windows -> four local logits.

    `present[B,120]` distinguishes padding from an observed zero-valued row.
    Input windows must already use the shared train-only bps normalization.
    No batch statistics or pooling across examples/tickers is permitted.
    """
    def __init__(self, width=32):
        super().__init__()
        if width < 4 or width % 4:
            raise ValueError('ResNet GroupNorm requires width divisible by four')
        self.encoder = nn.Sequential(
            nn.Conv1d(INPUT_WIDTH+1,width,7,stride=2,padding=3,bias=False),
            nn.GroupNorm(4,width),nn.ReLU(),ResidualBlock(width),
            nn.AvgPool1d(2),ResidualBlock(width),nn.AvgPool1d(2),
            ResidualBlock(width),nn.AdaptiveAvgPool1d(1))
        self.account = nn.Sequential(nn.Linear(7,width),nn.LayerNorm(width))
        self.holding = nn.Sequential(nn.Linear(11,width),nn.LayerNorm(width))
        self.execution = nn.Linear(len(EXECUTION_SCALE),width,bias=False)
        self.register_buffer('execution_scale',torch.tensor(EXECUTION_SCALE))
        self.heads = TickerHeads(width)

    def forward(self, windows, present, account, held, held_features, execution):
        b=windows.shape[0]
        if (windows.shape!=(b,120,INPUT_WIDTH) or present.shape!=(b,120) or
            present.dtype!=torch.bool or account.shape!=(b,7) or
            held.shape!=(b,) or held.dtype!=torch.bool or
            held_features.shape!=(b,11) or execution.shape!=(b,len(EXECUTION_SCALE))):
            raise ValueError('Malformed local ResNet observation')
        # Preserve actual zero candles while replacing absent padding only.
        inputs=torch.cat((windows.masked_fill(~present[:,:,None],0),
            present.to(windows.dtype)[:,:,None]),dim=-1)
        embedding=self.encoder(inputs.transpose(1,2)).squeeze(-1)  # [B,D]
        context=embedding+self.account(account.sign()*torch.log1p(account.abs()))
        features=held_features.clone()
        features[:,:3]=features[:,:3].sign()*torch.log1p(features[:,:3].abs())
        features[:,3:6]*=10
        context=context+self.holding(features)*held[:,None]
        context=context+self.execution(execution/self.execution_scale)
        return self.heads(context,held)
