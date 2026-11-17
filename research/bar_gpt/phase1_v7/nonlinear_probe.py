"""Fixed-budget frozen-encoder MLP probes; no validation tuning or future labels."""
import numpy as np
import torch
from .metrics import summaries


def probe(train_z,test_z,train_rows,test_rows,device='cpu'):
    y,names=summaries(train_rows);test_y,_=summaries(test_rows)
    xm=train_z.mean(0);xs=train_z.std(0).clip(1e-8)
    ym=y.mean(0);ys=y.std(0).clip(1e-8)
    x=torch.as_tensor((train_z-xm)/xs,dtype=torch.float32,device=device)
    target=torch.as_tensor((y-ym)/ys,dtype=torch.float32,device=device)
    test=torch.as_tensor((test_z-xm)/xs,dtype=torch.float32,device=device)
    torch.manual_seed(8219)
    model=torch.nn.Sequential(torch.nn.Linear(x.shape[1],128),torch.nn.GELU(),
        torch.nn.Linear(128,128),torch.nn.GELU(),torch.nn.Linear(128,8)).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    generator=torch.Generator(device=device).manual_seed(8219)
    for step in range(2000):
        ids=torch.randint(len(x),(1024,),device=device,generator=generator)
        optimizer.zero_grad(set_to_none=True)
        loss=torch.nn.functional.mse_loss(model(x[ids]),target[ids])
        if not torch.isfinite(loss):raise FloatingPointError('Nonfinite frozen probe')
        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),10);optimizer.step()
    with torch.no_grad():
        predictions=torch.cat([model(test[i:i+1024]) for i in range(0,len(test),1024)]).cpu().numpy()*ys+ym
    metrics={name:dict(mae=float(np.abs(predictions[:,j]-test_y[:,j]).mean()),
        r2=float(1-((predictions[:,j]-test_y[:,j])**2).sum()/max(((test_y[:,j]-test_y[:,j].mean())**2).sum(),1e-20))) for j,name in enumerate(names)}
    return dict(metrics=metrics,protocol=dict(hidden=[128,128],steps=2000,batch=1024,seed=8219,
        lr=.001,weight_decay=.001,calibration='training only',selection='fixed final update; no development/audit selection',
        labels='same eight within-window summaries as linear probes; no future or trading outcome labels'))
