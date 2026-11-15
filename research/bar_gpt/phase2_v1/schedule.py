"""Validation-aligned update cosine, with geometrically decreasing restart peaks."""
import math


class ValidationCosine:
    VERSION='validation-aligned-cosine-restarts-v1'

    def __init__(self,peak,minimum,period,warmup=2000,gamma=.9):
        if (not all(math.isfinite(v) for v in (peak,minimum,gamma))
                or not 0<minimum<=peak or not 0<gamma<=1
                or not isinstance(period,int) or period<1
                or not isinstance(warmup,int) or not 0<=warmup<period):
            raise ValueError('Invalid validation-aligned cosine schedule')
        self.peak,self.minimum,self.period,self.warmup,self.gamma=peak,minimum,period,warmup,gamma
        self.updates=self.epoch=0

    def bounds(self,epoch):
        scale=self.gamma**epoch
        return self.peak*scale,self.minimum*scale

    def value(self):
        cycle,position=divmod(self.updates,self.period)
        peak,minimum=self.bounds(cycle)
        if cycle==0 and position<self.warmup:
            return minimum+(peak-minimum)*(position+1)/self.warmup
        elapsed=position-(self.warmup if cycle==0 else 0)
        length=self.period-(self.warmup if cycle==0 else 0)
        fraction=elapsed/(length-1) if length>1 else float(cycle==0 and self.warmup>0)
        return minimum+(peak-minimum)*.5*(1+math.cos(math.pi*fraction))

    def state_dict(self):return dict(version=self.VERSION,**vars(self))

    def load_state_dict(self,state):
        if state.get('version')!=self.VERSION or any(
            state.get(k)!=getattr(self,k) for k in ('peak','minimum','period','warmup','gamma')):
            raise ValueError('Scheduler policy changed on resume')
        if (not isinstance(state.get('updates'),int) or state['updates']<0
                or not isinstance(state.get('epoch'),int) or state['epoch']<0):
            raise ValueError('Invalid scheduler update cursor')
        self.updates,self.epoch=state['updates'],state['epoch']


def schedule_manifest(args):
    period=args.validation_every
    return dict(version=FullPassCosine.VERSION if period else 'legacy-full-pass-epoch-cosine',
        epoch_kind='full dataset pass',full_passes=args.epochs,
        validation_every_updates=period,planned_optimizer_updates=None,
        restart_peak_decay=args.lr_restart_decay if period else None,
        initial_peak=args.learning_rate,initial_floor=args.min_learning_rate,
        floor_policy='initial_floor * restart_decay**completed_full_passes' if period else 'fixed legacy cosine floor',
        warmup_updates=args.warmup_updates,
        warmup_policy='once at initial training start; never repeated on cycle/resume',
        cursor_policy='advance every eligible origin; wrap only at full-data exhaustion',
        cosine_position='completed frozen training-block prefix / total training blocks',
        update_policy='held between validations after once-only startup warmup',
        validation='every N updates and full-pass end; no optimizer updates during validation')


class FullPassCosine:
    VERSION='full-pass-validation-gated-cosine-v2'

    def __init__(self,peak,minimum,period,warmup=2000,gamma=.9):
        ValidationCosine(peak,minimum,period,warmup,gamma)
        self.peak,self.minimum,self.period,self.warmup,self.gamma=peak,minimum,period,warmup,gamma
        self.updates=self.epoch=0
        self.fraction=0.

    def bounds(self,epoch):return self.peak*self.gamma**epoch,self.minimum*self.gamma**epoch

    def value(self):
        peak,minimum=self.bounds(self.epoch)
        if self.epoch==0 and self.updates<self.warmup:
            return minimum+(peak-minimum)*(self.updates+1)/self.warmup
        return minimum+(peak-minimum)*.5*(1+math.cos(math.pi*self.fraction))

    def validation(self,fraction):
        if not math.isfinite(fraction) or not self.fraction<=fraction<=1:
            raise ValueError('Full-pass cosine position moved backwards')
        self.fraction=float(fraction)

    def next_pass(self):self.epoch+=1;self.fraction=0.

    def state_dict(self):return dict(version=self.VERSION,**vars(self))

    def load_state_dict(self,state):
        if state.get('version')!=self.VERSION or any(state.get(k)!=getattr(self,k)
                for k in ('peak','minimum','period','warmup','gamma')):
            raise ValueError('Scheduler policy changed on resume')
        if (type(state.get('updates')) is not int or state['updates']<0
                or type(state.get('epoch')) is not int or state['epoch']<0
                or not math.isfinite(state.get('fraction',math.nan)) or not 0<=state['fraction']<=1):
            raise ValueError('Invalid full-pass scheduler state')
        self.updates,self.epoch,self.fraction=state['updates'],state['epoch'],state['fraction']


class PassProgress:
    """Exact frozen block traversal position; no payload scan/eligibility estimate."""
    def __init__(self,units):
        import numpy as np
        self.units=units
        if hasattr(units,'groups'):
            counts=np.concatenate([np.where(t['unit_split'].to_numpy()==0,
                t['ordinal'].list.len().to_numpy(),0) for _,_,t in units.groups])
        else:counts=np.array([sum(b[1]==0 for b in u.blocks) for u in units])
        self.prefix=np.concatenate(([0],np.cumsum(counts,dtype=np.int64)))
        if self.prefix[-1]<=0:raise ValueError('No selected training blocks')

    def fraction(self,cursor):
        ui,bi,_=cursor
        if not 0<=ui<len(self.units):raise ValueError('Invalid full-pass unit cursor')
        blocks=self.units[ui].blocks
        if not 0<=bi<len(blocks):raise ValueError('Invalid full-pass block cursor')
        return float((self.prefix[ui]+sum(b[1]==0 for b in blocks[:bi]))/self.prefix[-1])
