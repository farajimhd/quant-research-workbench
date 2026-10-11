"""Frozen 32-arm transformer search over the existing causal-window teacher.

Patch and inverted variants are adaptations, not reproductions of forecasting
papers. Inverted attention acts on projected latent channels, not raw channels.
"""
from dataclasses import dataclass, asdict
import torch
from torch import nn
from research.rl_trading.v6.architecture_benchmark import Candidate, WindowTeacher
from research.rl_trading.v6.channel_features import ChannelProjection

VERSION = 'rl-v6-transformer-search-32-v1'


@dataclass(frozen=True)
class TransformerSpec:
    family: str
    width: int
    depth: int
    heads: int
    expansion: int
    patch: int = 1
    pooling: str = 'last'

    def __post_init__(self):
        if self.family not in ('candle', 'patch', 'multiscale', 'latent_channel'):
            raise ValueError('Unknown transformer family')
        if self.width < 32 or self.width % 16 or self.width % self.heads:
            raise ValueError('Scalable width must divide attention heads')
        if not 1 <= self.depth <= 6 or self.expansion not in (2, 4):
            raise ValueError('Bounded transformer depth/expansion required')
        if self.patch not in (1, 4, 12) or 120 % self.patch or self.pooling not in ('last', 'mean'):
            raise ValueError('Invalid patch or pooling')


def specifications():
    # Identical capacity/attention settings across four representation families.
    settings = ((64, 2, 2, 2), (64, 3, 4, 4), (96, 2, 4, 4),
                (96, 4, 8, 2), (128, 2, 4, 4), (128, 4, 8, 4),
                (192, 3, 4, 4), (192, 4, 8, 2))
    result = []
    for family in ('candle', 'patch', 'multiscale', 'latent_channel'):
        for index, (width, depth, heads, expansion) in enumerate(settings):
            result.append(TransformerSpec(family, width, depth, heads, expansion,
                patch=(4 if index % 2 == 0 else 12) if family in ('patch', 'multiscale') else 1,
                pooling='last' if index % 2 == 0 else 'mean'))
    return result


class SearchEncoder(nn.Module):
    def __init__(self, spec, base, extra):
        super().__init__(); self.spec = spec
        width = spec.width
        self.project = ChannelProjection(width, base, extra)
        self.position = nn.Parameter(torch.randn(120, width) * .02) if spec.family != 'latent_channel' else None
        self.channel_time = nn.Linear(120, width) if spec.family == 'latent_channel' else None
        self.channel_identity = nn.Parameter(torch.randn(width, width) * .02) if spec.family == 'latent_channel' else None
        self.scales = (spec.patch, 1) if spec.family == 'multiscale' else (spec.patch,)
        self.patch_project = nn.ModuleDict({str(p): nn.Linear(p * width, width)
            for p in self.scales if p != 1})
        layer = nn.TransformerEncoderLayer(width, spec.heads, spec.expansion * width,
            dropout=0., activation='gelu', batch_first=True, norm_first=True)
        self.attention = nn.TransformerEncoder(layer, spec.depth, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)

    def encode_history(self, history, present):
        if history.shape[1:] != (120, self.spec.width) or present.shape != history.shape[:2] or present.dtype != torch.bool:
            raise ValueError('Expected 120 bounded history rows and boolean availability')
        x = history * present[..., None]
        if self.spec.family == 'latent_channel':
            # Every token contains only the already-observed history window.
            tokens = self.channel_time(x.transpose(1, 2)) + self.channel_identity
            output = self.attention(tokens).mean(1)
        else:
            outputs = []
            for patch in self.scales:
                count = 120 // patch
                valid = present.reshape(len(x), count, patch).any(-1)
                tokens = x.reshape(len(x), count, patch * self.spec.width)
                if patch != 1: tokens = self.patch_project[str(patch)](tokens)
                positions = self.position.reshape(count, patch, self.spec.width).mean(1)
                safe = valid.clone(); safe[:, 0] = True
                causal = torch.triu(torch.ones(count, count, device=x.device, dtype=torch.bool), 1)
                encoded = self.attention(tokens + positions, mask=causal, src_key_padding_mask=~safe)
                if self.spec.pooling == 'last':
                    indices = torch.arange(count, device=x.device)[None].expand_as(valid).masked_fill(~valid, 0).max(1).values
                    outputs.append(encoded[torch.arange(len(x), device=x.device), indices])
                else:
                    outputs.append((encoded * valid[..., None]).sum(1) / valid.sum(1).clamp_min(1)[:, None])
            output = torch.stack(outputs).mean(0)
        return torch.where(present.any(1)[:, None], self.norm(output), torch.zeros_like(output))


class TransformerTeacher(WindowTeacher):
    def __init__(self, spec, base, extra):
        super().__init__(Candidate('transformer', spec.width), base, extra)
        self.encoder = SearchEncoder(spec, base, extra)


def plan(base, extra):
    rows = []
    for index, spec in enumerate(specifications()):
        model = TransformerTeacher(spec, base, extra)
        rows.append(dict(architecture=f't{index+1:02d}-{spec.family}', width=spec.width,
            parameters=sum(p.numel() for p in model.parameters()), transformer=asdict(spec)))
    return rows


def build(row, base, extra):
    return TransformerTeacher(TransformerSpec(**row['transformer']), base, extra)


def selection(results):
    """Frozen multi-objective comparison; no composite profitability claim."""
    rows = []
    for result in results:
        val = result.get('validation')
        if not result['overfit_passed'] or not result['reload_exact'] or val is None: continue
        current = val['current']; entry = current['ENTRY']
        rows.append(dict(architecture=result['architecture'], entry_ap=entry['average_precision'],
            entry_f1=entry['f1'], current_macro_f1=sum(v['f1'] for v in current.values())/4,
            forecast_macro_f1=sum(v['f1'] for step in val['forecast'] for v in step.values())/20,
            entry_quality_mae=val['quality']['ENTRY'], exit_quality_mae=val['quality']['EXIT'],
            allocation_mae=val['allocation_mae'], wandb_url=result['wandb_url']))
    objectives = ('entry_ap','entry_f1','current_macro_f1','forecast_macro_f1',
                  'entry_quality_mae','exit_quality_mae','allocation_mae')
    def vector(row):
        return tuple(-row[key] if key.endswith('mae') else row[key] for key in objectives)
    frontier = []
    for row in rows:
        a = vector(row)
        if not any(all(x>=y for x,y in zip(vector(other),a)) and any(x>y for x,y in zip(vector(other),a)) for other in rows):
            frontier.append(row['architecture'])
    rows.sort(key=lambda row:(-row['entry_ap'],-row['entry_f1'],-row['current_macro_f1'],row['architecture']))
    return dict(status='exploratory', eligible=len(rows), ranked=rows, pareto_frontier=frontier,
        provisional_candidate=rows[0]['architecture'] if rows else None,
        rule='ENTRY_AP_then_ENTRY_F1_then_current_macro_F1; retain regression Pareto tradeoffs',
        independent_holdout=False, production_promotion=False)
