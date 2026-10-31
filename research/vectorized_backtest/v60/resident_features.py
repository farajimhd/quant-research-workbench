"""Original residency plus requested continuous indicator channels only."""
import numpy as np
import torch
from research.vectorized_backtest.v60.base_resident_features import ResidentFeatures as OriginalResident
from .features import ORIGINAL,VARIANTS,LEGACY
from research.vectorized_backtest.v60.features import BASE,HISTORY,PRICE_COLUMNS


class ResidentFeatures(OriginalResident):
    def __init__(self,data):
        super().__init__(data)
        values=getattr(data,'indicator_values',None)
        if values is not None:
            self.host['indicators']=torch.from_numpy(np.array(values,copy=True))
            self.bytes+=self.host['indicators'].numel()*4
            self.tensors=self.host

        enriched=getattr(data,'enriched_values',None)
        if enriched is not None:
            self.host['enriched']=torch.from_numpy(np.array(enriched,copy=True))
            self.bytes+=self.host['enriched'].numel()*4
            self.tensors=self.host

    def _values(self,rows,ids,clock,columns):
        if self.device.type=='cuda':
            from .feature_gather import project
            return project(self,rows,ids,clock,columns)
        return self._values_reference(rows,ids,clock,columns)

    def _values_reference(self,rows,ids,clock,columns):
        """Unchanged Torch projection, retained as CPU path and exact oracle."""
        old=[(i,c) for i,c in enumerate(columns) if c<len(ORIGINAL)]
        new=[(i,c-len(ORIGINAL)) for i,c in enumerate(columns) if len(ORIGINAL)<=c<len(LEGACY)]
        extra=[(i,c-len(LEGACY)) for i,c in enumerate(columns) if c>=len(LEGACY)]
        if extra:
            if 'enriched' not in self.tensors:raise ValueError('Enriched bank required')
            oldcols=[c for c in columns if c<len(LEGACY)]
            parts=[];known=[];positions=[]
            if oldcols:
                v,m=self._values_reference(rows,ids,clock,oldcols);parts.append(v);known.append(m);positions.extend(i for i,c in enumerate(columns) if c<len(LEGACY))
            fr=self.tensors['feature_row'][rows.clamp_min(0)]
            ix=torch.tensor([c for _,c in extra],device=self.device)
            v=self.tensors['enriched'][fr.clamp_min(0)[...,None],ix];m=torch.isfinite(v)&(rows>=0)[...,None]&(fr>=0)[...,None]
            parts.append(torch.where(m,v,0.));known.append(m);positions.extend(i for i,_ in extra)
            inverse=torch.tensor(np.argsort(positions),device=self.device)
            return torch.cat(parts,-1).index_select(-1,inverse),torch.cat(known,-1).index_select(-1,inverse)
        if not new:return self._original_values(rows,ids,clock,columns)
        if 'indicators' not in self.tensors:raise ValueError('Continuous indicator bank required for period variants')
        values=[];masks=[];order=[]
        if old:
            v,m=self._original_values(rows,ids,clock,[c for _,c in old]);values.append(v);masks.append(m);order.extend(i for i,_ in old)
        key=('indicator',tuple(columns))
        if key not in self.column_plans:
            self.column_plans[key]=(torch.tensor([c for _,c in new],device=self.device),torch.tensor(np.argsort(order+[i for i,_ in new]),device=self.device))
        indices,inverse=self.column_plans[key]
        feature_rows=self.tensors['feature_row'][rows.clamp_min(0)]
        v=self.tensors['indicators'][feature_rows.clamp_min(0)[...,None],indices]
        m=torch.isfinite(v)&(rows>=0)[...,None]&(feature_rows>=0)[...,None]
        values.append(torch.where(m,v,0.));masks.append(m)
        return torch.cat(values,-1).index_select(-1,inverse),torch.cat(masks,-1).index_select(-1,inverse)

    def _original_values(self,rows,ids,clock,columns):
        values,masks=super()._values(rows,ids,clock,columns)
        # Recompute directly in float64; do not add one to a rounded shifted float.
        for out,c in enumerate(columns):
            if len(BASE)+len(HISTORY)<=c<len(ORIGINAL)-11:
                channel=PRICE_COLUMNS[c-len(BASE)-len(HISTORY)]
                if 180<=channel<211:
                    raw=self.tensors['history'][ids,channel].to(torch.float64)
                    price=self.tensors['mark'][rows.clamp_min(0)]
                    ratio=(raw/price).to(torch.float32)
                    values[...,out]=torch.where(masks[...,out],ratio,0.)
        return values,masks
