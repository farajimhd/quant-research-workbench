"""Evaluation-only fixed-origin and future-informed shifted-actual controls."""
import math
import torch
from ..phase1_v11.transforms import PRICES
from ..v5.shards.phase1_contract import CHANNELS


class ForecastMetrics:
    VERSION = 'shifted-actual-direction-v1'
    def __init__(self, device):
        self.device = device
        self.sums = {}

    def add_sum(self, name, value, mask):
        value = torch.where(mask, value, 0).double().sum(0)
        if name not in self.sums:
            self.sums[name] = torch.zeros_like(value)
        self.sums[name] += value

    @torch.no_grad()
    def add(self, predicted, target):
        p = predicted.double()
        price = torch.zeros(18, device=p.device, dtype=torch.bool)
        price[list(PRICES)] = True
        raw_p = torch.where(price, p.exp(), p.expm1())
        actual = target['raw'].double()
        valid = target['valid']
        last = target['last_raw'].double()[:, None]
        last_valid = target['last_valid'][:, None]
        previous = torch.cat((last, actual[:, :-1]), 1)
        previous_valid = torch.cat((last_valid, valid[:, :-1]), 1)
        for key, baseline, bv in (('last_observed', last.expand_as(actual), last_valid),
                                  ('shifted_actual_future_informed', previous, previous_valid)):
            mask = valid & bv
            stem = 'baseline/' + key + '/'
            self.add_sum(stem+'count', torch.ones_like(actual), mask)
            self.add_sum(stem+'raw_absolute_error', (baseline-actual).abs(), mask)
            self.add_sum(stem+'model_raw_absolute_error', (raw_p-actual).abs(), mask)
            # Valid prices are positive; zero activity is allowed.
            safe = torch.where(mask, baseline, 1)
            logs = torch.where(price, safe.log(), safe.log1p())
            self.add_sum(stem+'log_absolute_error', (logs-target['logs'].double()).abs(), mask)

        pm = valid & price & (actual > 0)
        relative = (raw_p-actual).abs()/torch.where(pm, actual, 1)
        self.add_sum('price/count', torch.ones_like(actual), pm)
        for tolerance in (.0005, .001, .002):
            self.add_sum(f'price/within_{100*tolerance:g}pct', (relative <= tolerance).double(), pm)

        # Origin direction is deployable; path direction compares successive
        # model predictions, never substitutes actual targets into prediction.
        last_log = target['last'].double()[:, None]
        pred_previous = torch.cat((last_log, p[:, :-1]), 1)
        for key, pred_change, true_change, mask in (
            ('from_origin', p-last_log, actual-last, pm & last_valid),
            ('consecutive_path', p-pred_previous, actual-previous, pm & previous_valid)):
            stem = 'direction/'+key+'/'
            moving = mask & (true_change != 0)
            equal = pred_change.sign() == true_change.sign()
            for name, value, m in (
                ('count', torch.ones_like(actual), mask),
                ('moving_count', torch.ones_like(actual), moving),
                ('unchanged_count', (true_change == 0).double(), mask),
                ('correct_3way', equal.double(), mask),
                ('correct_moving', equal.double(), moving),
                ('actual_up', (true_change > 0).double(), moving)):
                self.add_sum(stem+name, value, m)

    def result(self):
        arrays = {k:v.cpu().numpy() for k,v in self.sums.items()}
        result = {}
        def ratio(a,b):
            return float(a/b) if b and math.isfinite(float(a)) else None
        # All-channel aggregate controls; per-horizon detail for trade/bid/ask close.
        for c, channel in enumerate(CHANNELS):
            scopes = [('all', slice(None))] + ([(f'bar_{h+1}',h) for h in range(64)] if c in (3,9,15) else [])
            for scope,h in scopes:
                def total(name):return arrays[name][h,c].sum()
                for key in ('last_observed','shifted_actual_future_informed'):
                    src='baseline/'+key+'/'
                    out=f'forecast/baseline/{key}/{scope}/{channel}/'
                    n=total(src+'count');base=total(src+'raw_absolute_error');ours=total(src+'model_raw_absolute_error')
                    result[out+'count']=int(n)
                    result[out+'raw_mae']=ratio(base,n)
                    result[out+'model_raw_mae_same_mask']=ratio(ours,n)
                    result[out+'log_mae']=ratio(total(src+'log_absolute_error'),n)
                    result[out+'raw_mae_skill']=float(1-ours/base) if base>0 else None
                if c not in (3,9,15):continue
                for tolerance in (.05,.1,.2):
                    value=ratio(total(f'price/within_{tolerance:g}pct'),total('price/count'))
                    result[f'forecast/tolerance/{scope}/{channel}/within_{tolerance:g}pct']=None if value is None else 100*value
                for key in ('from_origin','consecutive_path'):
                    src='direction/'+key+'/'
                    out=f'forecast/direction/{key}/{scope}/{channel}/'
                    for name in ('count','moving_count','unchanged_count'):result[out+name]=int(total(src+name))
                    for name,num,den in (('accuracy_3way_pct','correct_3way','count'),('accuracy_moving_pct','correct_moving','moving_count'),('actual_up_moving_pct','actual_up','moving_count')):
                        value=ratio(total(src+num),total(src+den));result[out+name]=None if value is None else 100*value
        if any(v is not None and not math.isfinite(v) for v in result.values()):raise FloatingPointError('Nonfinite forecast diagnostics')
        return result
