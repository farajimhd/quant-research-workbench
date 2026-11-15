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
    return dict(version=ValidationCosine.VERSION if period else 'legacy-full-pass-epoch-cosine',
        epoch_kind='validation-aligned optimizer-update interval' if period else 'full dataset pass',
        cycle_updates=period,logical_epochs=args.epochs,
        planned_optimizer_updates=args.epochs*period if period else None,
        restart_peak_decay=args.lr_restart_decay if period else None,
        initial_peak=args.learning_rate,initial_floor=args.min_learning_rate,
        floor_policy='initial_floor * restart_decay**cycle' if period else 'fixed legacy cosine floor',
        warmup_updates=args.warmup_updates,
        warmup_policy='once at initial training start; never repeated on cycle/resume',
        cursor_policy='continue data across cycles; wrap only at full-data exhaustion',
        validation='one full validation at each cycle end; no optimizer/scheduler updates during validation')
