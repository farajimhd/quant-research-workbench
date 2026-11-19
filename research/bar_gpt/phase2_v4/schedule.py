"""Update-based startup warmup, then a fixed learning rate."""
import math


class FixedWarmup:
    VERSION='phase2-v4-fixed-lr-warmup-v1'
    def __init__(self,peak,minimum,period,warmup=1000,gamma=.9):
        if not 0<minimum<=peak or not all(math.isfinite(x) for x in (peak,minimum)) or warmup<0:raise ValueError('Invalid fixed schedule')
        self.peak,self.minimum,self.period,self.warmup,self.gamma=peak,minimum,period,warmup,gamma
        self.updates=self.epoch=0;self.fraction=0.
    def bounds(self,epoch):return self.peak,self.peak
    def value(self):
        return self.minimum+(self.peak-self.minimum)*(self.updates+1)/self.warmup if self.updates<self.warmup else self.peak
    def validation(self,fraction):
        if not self.fraction<=fraction<=1:raise ValueError('Invalid progress')
        self.fraction=float(fraction)
    def next_pass(self):self.epoch+=1;self.fraction=0.
    def state_dict(self):return dict(version=self.VERSION,**vars(self))
    def load_state_dict(self,state):
        if state.get('version')!=self.VERSION or any(state.get(k)!=getattr(self,k) for k in ('peak','minimum','period','warmup','gamma')):raise ValueError('Schedule changed')
        if type(state.get('updates')) is not int or state['updates']<0 or type(state.get('epoch')) is not int or state['epoch']<0 or not 0<=state['fraction']<=1:raise ValueError('Invalid scheduler state')
        self.updates,self.epoch,self.fraction=state['updates'],state['epoch'],state['fraction']


def schedule_manifest(a):
    return dict(version=FixedWarmup.VERSION,full_passes=a.epochs,epoch_kind='full dataset pass',
        peak=a.learning_rate,warmup_start=a.min_learning_rate,warmup_updates=a.warmup_updates,
        after_warmup='fixed learning rate; no full-pass-dependent decay',validation_every_updates=a.validation_every)
