"""Consistent-mask-scale encoders; decoder receives only learned 256D values."""
import torch
from torch import nn
from ..phase1_v16.model import Autoencoder as Previous, TemporalSlots


class CenteredSlots(TemporalSlots):
    def forward(self, visible_latents, visible_metadata, pos):
        x=torch.cat(((visible_latents-self.mean)/self.scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,
                            enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1)).float()
        summary=h[:,0];tokens=h[:,1:]
        residual=tokens-tokens.mean(1,keepdim=True)
        aligned=residual.new_zeros(len(x),64,residual.shape[-1])
        aligned=aligned.scatter(1,pos[...,None].expand_as(residual),residual)
        return self.global_readout(summary)+self.readout(aligned.flatten(1))


class MeanFilledSlots(TemporalSlots):
    def forward(self,visible_latents,visible_metadata,pos):
        x=torch.cat(((visible_latents-self.mean)/self.scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,
                            enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1))[:,1:].float()
        # Impute only missing encoded states from visible states, before the
        # learned bottleneck. No raw feature, target, or decoder side channel.
        aligned=h.mean(1,keepdim=True).expand(-1,64,-1)
        aligned=aligned.scatter(1,pos[...,None].expand_as(h),h)
        embedding=self.readout(aligned.flatten(1))
        if hasattr(self,'mask_adapter') and pos.shape[1]<64:
            embedding=embedding+(1-pos.shape[1]/64)*self.mask_adapter(embedding)
        return embedding


class ControlVariateSlots(TemporalSlots):
    def forward(self,visible_latents,visible_metadata,pos):
        x=torch.cat(((visible_latents-self.mean)/self.scale,visible_metadata),-1)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,
                            enabled=x.is_cuda and self.config.precision=='bf16'):
            h=self.stem(x)+self.position[0,pos]
            h=self.blocks(torch.cat((self.cls.expand(len(x),-1,-1),h),1))[:,1:].float()
        if pos.shape[1]==64:
            aligned=h.new_zeros(len(h),64,h.shape[-1]).scatter(1,pos[...,None].expand_as(h),h)
        else:
            mean=h.mean(1,keepdim=True)
            # Preserve count correction for deviations, but not the common
            # component. The reference is visible-only and stays in encoder.
            visible=mean+(64/pos.shape[1])*(h-mean)
            aligned=mean.expand(-1,64,-1).scatter(1,pos[...,None].expand_as(h),visible)
        return self.readout(aligned.flatten(1))


class Autoencoder(Previous):
    def __init__(self,c,mean,scale,architecture='scaled'):
        super().__init__(c,mean,scale,'scaled_slots_price')
        if architecture not in ('scaled','unscaled','centered','mean_filled','control_variate','mask_adapter','count_nullspace'):raise ValueError(architecture)
        self.architecture=architecture
        self.encoder.count_scaled=architecture=='scaled'
        if architecture=='centered':
            self.encoder.__class__=CenteredSlots
            self.encoder.global_readout=nn.Linear(c.width,c.embedding)
        if architecture=='mean_filled':self.encoder.__class__=MeanFilledSlots
        if architecture=='control_variate':self.encoder.__class__=ControlVariateSlots
        if architecture in ('mask_adapter','count_nullspace'):
            self.encoder.__class__=MeanFilledSlots
            self.encoder.mask_adapter=nn.Linear(c.embedding,c.embedding)
            nn.init.zeros_(self.encoder.mask_adapter.weight);nn.init.zeros_(self.encoder.mask_adapter.bias)
        if architecture=='count_nullspace':
            self.count_head=nn.Linear(c.embedding,64)
            nn.init.zeros_(self.count_head.weight);nn.init.zeros_(self.count_head.bias)
            self.register_buffer('count_basis',torch.zeros(16))

    def decode(self,embedding):
        prediction=super().decode(embedding)
        if hasattr(self,'count_head'):
            prediction=prediction+self.count_head(embedding.float())[...,None]*self.count_basis
        return prediction

    @torch.no_grad()
    def initialize_count_basis(self,teacher):
        # Nullspace of the twelve price and three quantity coordinates in the
        # frozen teacher decoder. Counts may change; nothing is appended to E.
        matrix=teacher.decoder.weight.detach().double()*teacher.scale[:18,None].double()*self.encoder.scale.double()[None,:]
        protected=[i for i in range(18) if i not in (5,11,17)]
        rows=matrix[protected];rows=rows/rows.norm(dim=1,keepdim=True)
        _,_,v=torch.linalg.svd(rows,full_matrices=True)
        basis=v[-1];effect=matrix[11]@basis
        if effect.abs()<1e-8:raise ValueError('No usable count-only direction')
        basis=basis/effect
        if (matrix[protected]@basis).abs().max()>1e-8:raise ValueError('Count basis leaks into protected coordinates')
        self.count_basis.copy_(basis.float())
        return dict(count_effects=(matrix[[5,11,17]]@basis).cpu().tolist(),basis_norm=float(basis.norm()))


def architecture(contract):
    return contract.get('architecture','scaled' if contract.get('encoder_count_scaled',True) else 'unscaled')


def initialize_parent(model,state):
    """Optional learned bottleneck expansion with zero added decoder columns."""
    own=model.state_dict();changed=[]
    for key,value in state.items():
        if key not in own:raise ValueError('Unknown parent parameter: '+key)
        if own[key].shape==value.shape:own[key]=value
        elif key in ('encoder.readout.weight','encoder.readout.bias') and own[key].shape[0]>value.shape[0] and own[key].shape[1:]==value.shape[1:]:
            own[key][:value.shape[0]]=value;changed.append(key)
        elif key=='decoder.weight' and own[key].shape[0]==value.shape[0] and own[key].shape[1]>value.shape[1]:
            own[key].zero_();own[key][:,:value.shape[1]]=value;changed.append(key)
        else:raise ValueError('Unqualified parent geometry: '+key)
    missing=set(own)-set(state)
    if missing-set(('encoder.mask_adapter.weight','encoder.mask_adapter.bias','count_head.weight','count_head.bias','count_basis')):raise ValueError('Parent is missing architecture parameters')
    changed.extend(sorted(missing))
    model.load_state_dict(own);return changed


def load_exported_encoder(path,device='cpu'):
    from ..phase1_v12.model import Config
    saved=torch.load(path,map_location='cpu',weights_only=False)
    state=saved['encoder']
    model=Autoencoder(Config(**saved['config']),state['mean'],state['scale'],saved['architecture'])
    model.encoder.load_state_dict(state)
    return model.encoder.to(device).eval()
