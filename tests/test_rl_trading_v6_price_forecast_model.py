import numpy as np
import pytest
import torch
from research.rl_trading.v6.price_forecast_model import PriceForecastTeacher
from research.rl_trading.v6.run_hierarchy_bias_campaign import factor_epoch
from research.rl_trading.v6.run_bias_campaign import tensors,forward


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_price_teacher_forcing_never_changes_current_action_and_real_optimizer_updates_decoder(device):
    torch.set_num_threads(2);torch.manual_seed(17);n=16;action=np.tile(np.arange(4),4)
    held=np.zeros((n,11),np.float32);held[action>=2,0]=1
    source=dict(features=np.random.default_rng(17).normal(size=(n+1,147)).astype(np.float32),
        windows=np.arange(1,n+1)[:,None].repeat(120,axis=1),market=np.zeros((n,147),np.float32),held=held,
        action=action,weight=np.ones(n,np.float32),future=np.full((n,5),-1),quality=np.zeros(n,np.float32),
        ratio=np.full(n,np.nan,np.float32),episode=['fixture']*n)
    data=tensors(source,dict(mean=[0.]*147,std=[1.]*147),device);eligible=action!=1;eligible[1]=True
    data.update(entry_1a=torch.as_tensor(action==0,device=device),episode_selected=torch.as_tensor(eligible,device=device),
        price_return_bps=torch.arange(n*5,device=device,dtype=torch.float32).reshape(n,5),price_mask=torch.ones((n,5),device=device,dtype=torch.bool))
    model=PriceForecastTeacher('tcn',width=8).to(device);model.eval();index=torch.arange(n,device=device)
    result=forward(model,data,index);forced=model.decode_price(result['context'],forcing=data['price_return_bps'],forcing_mask=data['price_mask'])
    torch.testing.assert_close(forced[:,0],result['price_free'][:,0],atol=0,rtol=0)
    assert not torch.equal(forced[:,1:],result['price_free'][:,1:])
    torch.testing.assert_close(forward(model,data,index)['logit'],result['logit'],atol=0,rtol=0)
    before={k:v.detach().clone() for k,v in model.named_parameters()}
    fit=factor_epoch(model,data,torch.optim.AdamW(model.parameters(),lr=.001),epoch=1,batch_size=16,price_scale=10.)
    assert fit['autoregressive_prices_supervised']
    assert not torch.equal(before['price_output.weight'],model.price_output.weight)
    assert not torch.equal(before['timing.weight'],model.timing.weight)
    for name,value in model.named_parameters():
        if name.startswith(('size.','forecast.','forecast_gru.','heads.quality.')):torch.testing.assert_close(value,before[name],atol=0,rtol=0)
    with pytest.raises(ValueError,match='Five'):model.decode_price(result['context'],forcing=torch.ones((n,4),device=device))
