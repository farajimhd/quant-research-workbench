from dataclasses import dataclass


@dataclass
class Config:
    width:int=384
    layers:int=8
    embedding:int=1024
    latent_tokens:int=4
    visible:int=16
    precision:str='bf16'
    dropout:float=.05
    reference_weight:float=1.
    center:bool=True
    input_asinh:bool=True
    readout:str='latents'
    input_price_scale:float=100.
    visible_weight:float=0.

