from dataclasses import dataclass
from ..phase2_unmasked_v1.config import Config as UnmaskedConfig

VERSION='bargpt-phase2-bars-v1'

@dataclass(frozen=True)
class Config(UnmaskedConfig):
    def manifest(self):
        result=super().manifest()
        result.update(version=VERSION,predictor='Linear512->256 GELU Linear256->1159',
            loss=dict(normalized_bars_weight=1.,references_weight=1.,source_valid_masks=True),
            objective='source-valid normalized64x18 future bars MSE + seven future-reference-change MSE; weights1:1',
            output=dict(bars=[64,18],references=7,reference_basis='known latest1s context-window references',
                price_change='100*ln(future_price_reference/origin_price_reference)',
                activity_change='ln(1+future_reference)-ln(1+origin_reference)'),
            normalization='exact V4 local source-valid medians; prices100ln(P/target_ref); activityln1p(value)-ln1p(target_ref)',
            initialization='fresh new run only; no incompatible embedding-target checkpoint resume',
            labels='strictly next64 observed1s bars inside source block; no future embedding target requested',
            deployment='raw forecasts use PREDICTED future references and known historical anchors; no actual future references/clocks')
        return result
