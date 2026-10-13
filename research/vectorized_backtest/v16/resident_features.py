"""Original residency plus requested continuous indicator channels only."""
import numpy as np
import torch
from research.vectorized_backtest.v7.resident_features import ResidentFeatures as OriginalResident
from .features import ORIGINAL,VARIANTS


class ResidentFeatures(OriginalResident):
    def __init__(self,data):
        super().__init__(data)
        values=getattr(data,'indicator_values',None)
        if values is not None:
            self.host['indicators']=torch.from_numpy(np.array(values,copy=True))
            self.bytes+=self.host['indicators'].numel()*4
            self.tensors=self.host

    def _values(self,rows,ids,clock,columns):
        old=[(i,c) for i,c in enumerate(columns) if c<len(ORIGINAL)]
        new=[(i,c-len(ORIGINAL)) for i,c in enumerate(columns) if c>=len(ORIGINAL)]
        if not new:return super()._values(rows,ids,clock,columns)
        if 'indicators' not in self.tensors:raise ValueError('Continuous indicator bank required for period variants')
        values=[];masks=[];order=[]
        if old:
            v,m=super()._values(rows,ids,clock,[c for _,c in old]);values.append(v);masks.append(m);order.extend(i for i,_ in old)
        key=('indicator',tuple(columns))
        if key not in self.column_plans:
            self.column_plans[key]=(torch.tensor([c for _,c in new],device=self.device),torch.tensor(np.argsort(order+[i for i,_ in new]),device=self.device))
        indices,inverse=self.column_plans[key]
        feature_rows=self.tensors['feature_row'][rows.clamp_min(0)]
        v=self.tensors['indicators'][feature_rows.clamp_min(0)[...,None],indices]
        m=torch.isfinite(v)&(rows>=0)[...,None]&(feature_rows>=0)[...,None]
        values.append(torch.where(m,v,0.));masks.append(m)
        return torch.cat(values,-1).index_select(-1,inverse),torch.cat(masks,-1).index_select(-1,inverse)
