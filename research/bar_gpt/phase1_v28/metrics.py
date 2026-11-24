"""Keep original reconstruction metrics comparable; log new objective separately."""
import torch
from ..phase0_v7.metrics import Metrics as Original
from .objectives import objective


class Metrics(Original):
    def __init__(self,device='cpu'):
        super().__init__(device)
        self.objective_sums=torch.zeros(5,device=device,dtype=torch.float64)
        self.evaluated_bars=torch.zeros((),device=device,dtype=torch.int64)

    @torch.no_grad()
    def add(self,model,out,values,valid,batch):
        # A masked diagnostic reports hidden bars only. Adjacent-direction metrics
        # then require both endpoints hidden; all-visible reports remain unchanged.
        selected=valid & out['hidden'][...,None] if model.eval_mask else valid
        super().add(model,out,values,selected,batch)
        self.evaluated_bars+=selected[...,:18].any(-1).sum()
        total,parts=objective(model,out,values,valid)
        self.objective_sums[:4]+=torch.stack((total,parts['direction'],parts['range'],parts['ohlc'])).double()*len(values)
        self.objective_sums[4]+=parts['source_inconsistent_ohlc'].double()

    def result(self):
        result=super().result()
        for name,value in zip(('total','direction','range','ohlc'),(self.objective_sums[:4]/self.windows).cpu().tolist()):
            result['metrics']['objective/'+name]=value
        result['metrics']['objective/source_inconsistent_ohlc_count']=int(self.objective_sums[4])
        result['metrics']['coverage/evaluated_bars']=int(self.evaluated_bars)
        # Main displayed/selection loss remains the original reconstruction loss;
        # objective/total includes the added penalties and is separately labelled.
        return result
