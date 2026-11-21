import torch
from ..phase1_v12.model import Encoder as BaseEncoder,Autoencoder as Base


class Encoder(BaseEncoder):
    def __init__(self,c,mean,scale,input_center,input_scale):
        super().__init__(c,mean,scale)
        self.register_buffer('input_center',torch.as_tensor(input_center,dtype=torch.float32).clone())
        self.register_buffer('input_scale',torch.as_tensor(input_scale,dtype=torch.float32).clone())
        if self.input_center.shape!=(16,) or self.input_scale.shape!=(16,):raise ValueError('Expected16 input dimensions')
        if not torch.isfinite(self.input_center).all() or not torch.isfinite(self.input_scale).all() or not (self.input_scale>0).all():raise ValueError('Invalid fixed input statistics')

    def forward(self,visible_latents,visible_metadata,pos):
        # Only visible tokens are normalized. Target coordinates remain unchanged.
        x=torch.cat(((visible_latents-self.input_center)/self.input_scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1))[:,0]
        return self.readout(h.float())


class Autoencoder(Base):
    def __init__(self,c,mean,scale,input_center,input_scale):
        super().__init__(c,mean,scale)
        prior=self.encoder.state_dict()
        self.encoder=Encoder(c,mean,scale,input_center,input_scale)
        missing,unexpected=self.encoder.load_state_dict(prior,strict=False)
        if set(missing)!={'input_center','input_scale'} or unexpected:raise AssertionError('Unexpected initialization mismatch')


@torch.no_grad()
def fit_inputs(latents,valid,mean,scale):
    values=latents[valid]
    if not torch.isfinite(values).all():raise ValueError('Nonfinite teacher latent')
    quantiles=torch.stack([torch.quantile(values[:,i],values.new_tensor([.25,.5,.75])) for i in range(16)],1)
    minimum=values.amin(0);maximum=values.amax(0)
    specs={'zscore':(values.new_tensor(mean),values.new_tensor(scale)),
           'none':(torch.zeros_like(minimum),torch.ones_like(minimum)),
           'minmax':(minimum,maximum-minimum),
           'robust':(quantiles[1],quantiles[2]-quantiles[0])}
    # No clipping, winsorization, validation fitting or unreported fallback.
    if any(not bool((s>0).all()) for c,s in specs.values()):raise ValueError('Degenerate input scale requires explicit design decision')
    return {name:dict(center=c.tolist(),scale=s.tolist()) for name,(c,s) in specs.items()}
