"""Auxiliary autoregressive price decoder; never an action observation."""
import torch
from torch import nn
from research.rl_trading.v6.run_hierarchy_bias_campaign import HierarchyTeacher


class PriceForecastTeacher(HierarchyTeacher):
    def __init__(self,architecture='tcn',*,width=128):
        super().__init__(architecture,width=width)
        self.price_gru=nn.GRUCell(width+2,width);self.price_output=nn.Linear(width,1)

    def decode_price(self,context,*,forcing=None,forcing_mask=None):
        if forcing is not None and (forcing.shape!=(len(context),5) or forcing_mask is None or forcing_mask.shape!=forcing.shape):
            raise ValueError('Five exact-clock forcing targets and masks required')
        state=context;previous=context.new_zeros((len(context),1));available=torch.zeros_like(previous);values=[]
        for step in range(5):
            if step and forcing is not None:
                use=forcing_mask[:,step-1,None]
                previous=torch.where(use,forcing[:,step-1,None],previous)
                available=use.to(context.dtype)
            state=self.price_gru(torch.cat((context,previous,available),1),state)
            previous=self.price_output(state);values.append(previous)
            if forcing is None:available=torch.ones_like(previous)
        return torch.cat(values,1)

    def forward(self,windows,present,market,held,**kwargs):
        result=super().forward(windows,present,market,held,**kwargs)
        result['price_free']=self.decode_price(result['context'])
        return result
