"""Target-reference-conditioned decoded diagnostics, never an optimization loss."""
import math
import torch
from ..v5.native_validation_v3 import inverse
from ..v5.price_relative_v3 import PriceRelative, CONTRACT as PRICE_CONTRACT
from ..v5.shards.phase1_contract import CHANNELS

CONTRACT = dict(
    version='phase2-v1-target-conditioned-decoded-v1',
    population='all source-valid observations in every eligible validation target; overlap retained',
    targets='next 64 observed 1s bars; raw stored session split-adjusted units',
    decoder='frozen V4 decoder from exact encoder checkpoint; all 18 feature IDs',
    conditioning='actual target-window Float64 price/quantity references and actual UTC time features',
    isolation='target references/clocks only in evaluation; never phase2 inputs or optimization loss',
    comparison='predicted latent and actual target latent decoded against the SAME original raw bars',
    units='price USD/share; quantities as stored; counts events/bar; negative predictions not clipped',
    invalid='source-invalid observations excluded; counts exposed; nonfinite predictions counted, metrics unavailable',
    horizon='1..64 observed bars, not wall-clock seconds',
    price_relative=PRICE_CONTRACT,
)


class DecodedMetrics:
    """Bounded observation-weighted MAE/RMSE, horizon summaries and price histograms."""
    def __init__(self, device):
        # Two branches x channel x future position. No corpus-sized buffers.
        self.sums = torch.zeros((2,18,64), device=device, dtype=torch.float64)
        self.squares = torch.zeros_like(self.sums)
        self.counts = torch.zeros_like(self.sums, dtype=torch.int64)
        self.bad = torch.zeros_like(self.counts)
        self.relative = [PriceRelative(), PriceRelative()]
        self.origins = 0

    @torch.no_grad()
    def add(self, decoder, predicted, target, evidence):
        if decoder.training or any(p.requires_grad for p in decoder.parameters()):
            raise ValueError('Reconstruction decoder must be frozen and eval')
        if predicted.shape != target.shape or predicted.shape != (len(predicted),512):
            raise ValueError('Expected aligned predicted/target 512 embeddings')
        mask, raw = evidence['valid'], evidence['raw']
        if raw.shape != (len(target),18,64) or mask.shape != raw.shape or mask.dtype != torch.bool:
            raise ValueError('Misaligned raw reconstruction evidence')
        if not torch.isfinite(raw[mask]).all():raise ValueError('Nonfinite observed raw target')
        ids = torch.arange(18, device=predicted.device).expand(len(target),-1)
        # Evaluation in Float32, inverses/reductions in Float64. No autocast or clipping.
        with torch.autocast(predicted.device.type, enabled=False):
            for branch, embedding in enumerate((predicted, target)):
                normalized = decoder(embedding.float(), ids, evidence['time'].float(), evidence['time_valid'])
                decoded = inverse(normalized, evidence['reference'], evidence['stats'])
                error = (decoded-raw.double()).abs()
                square = error.square()
                finite = torch.isfinite(error) & torch.isfinite(square)
                self.counts[branch] += mask.sum(0)
                self.bad[branch] += (mask & ~finite).sum(0)
                self.sums[branch] += torch.where(mask & finite, error, 0).sum(0)
                self.squares[branch] += torch.where(mask & finite, square, 0).sum(0)
                self.relative[branch].add(error, raw, mask,
                    torch.zeros(len(target),device=raw.device,dtype=torch.int64))
        self.origins += len(target)

    def result(self):
        result = dict(decoded_origins=self.origins)
        for b, stem in enumerate(('decoded', 'decoder_baseline')):
            s,q,n,bad=(x[b].cpu() for x in (self.sums,self.squares,self.counts,self.bad))
            def values(path, total, square, count, overflow):
                count, overflow = int(count), int(overflow)
                ok = count>0 and not overflow and math.isfinite(float(total)) and math.isfinite(float(square))
                result[f'{stem}_raw_mae/{path}'] = float(total)/count if ok else None
                result[f'{stem}_raw_rmse/{path}'] = math.sqrt(float(square)/count) if ok else None
                result[f'{stem}_raw_counts/{path}'] = count
                result[f'{stem}_raw_overflows/{path}'] = overflow
            for c,name in enumerate(CHANNELS):
                values(f'1s/{name}',s[c].sum(),q[c].sum(),n[c].sum(),bad[c].sum())
                for h in range(64):
                    values(f'1s/{name}/horizon_{h+1:02d}',s[c,h],q[c,h],n[c,h],bad[c,h])
            # Reuse shared price-only, raw-unit MAPE/median/P90/band semantics.
            result.update(self.relative[b].metrics(f'{stem}_raw_mae',('1s',),CHANNELS))
        return result
