import torch
from ..phase0_v2.model import Autoencoder as Previous
from ..phase1_v11.transforms import coordinates

ACTIVITY=(4,5,10,11,16,17)


def relative_input(log1p_activity,reference):
    # Stable log(1+q/r), including exact q=0; no ceiling on valid quantities.
    return torch.logaddexp(torch.log(-torch.expm1(-log1p_activity))+log1p_activity-reference.log(),torch.zeros_like(log1p_activity))


class Autoencoder(Previous):
    def __init__(self,parameters,config,input_calibration=None):
        super().__init__(parameters,config)
        self.use_reference=input_calibration is not None
        self.register_buffer('activity_indices',torch.tensor(ACTIVITY))
        self.register_buffer('input_reference',torch.tensor(input_calibration['reference'] if input_calibration else [1.]*6,dtype=torch.float64))
        self.register_buffer('activity_input_mean',torch.tensor(input_calibration['mean'] if input_calibration else [0.]*6,dtype=torch.float32))
        self.register_buffer('activity_input_scale',torch.tensor(input_calibration['scale'] if input_calibration else [1.]*6,dtype=torch.float32))

    def encode(self,values,valid):
        if not self.use_reference:return super().encode(values,valid)
        code,cm=coordinates(values[...,:18],valid[...,:18])
        features=torch.cat((code,values[...,18:]),-1);mask=torch.cat((cm,valid[...,18:]),-1)
        scaled=(features-self.mean)/self.scale
        activity=relative_input(code.index_select(-1,self.activity_indices).double(),self.input_reference).float()
        scaled=scaled.scatter(-1,self.activity_indices.expand(*scaled.shape[:-1],6),(activity-self.activity_input_mean)/self.activity_input_scale)
        return self.encoder(torch.cat((torch.where(mask,scaled,0),valid.float()),-1))
