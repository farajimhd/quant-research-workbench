"""Existing full validation plus native-GPU price-band and shape-amplitude audit."""
import time
import torch
from ..phase1_v21.experiment import evaluate as base_evaluate
from ..phase1_v12.model import positions
from ..phase1_v11.transforms import inverse,coordinates,PRICES


@torch.no_grad()
def evaluate(model,teacher,data):
    detail,metrics=base_evaluate(model,teacher,data);started=time.perf_counter();model.eval();logs=[];codes=[]
    for i in range(0,len(data[0]),512):
        x,v=data[0][i:i+512],data[1][i:i+512]
        output=model(x,v,positions(len(x),x.device,64))[2];logs.append(output['logs']);codes.append(output['code'])
    logs=torch.cat(logs);code=torch.cat(codes);prediction=inverse(logs)
    raw=data[2].double();valid=data[1][...,:18];audit={}
    target=raw[...,list(PRICES)];pred=prediction[...,list(PRICES)];mask=valid[...,list(PRICES)]
    audit['nonpositive_price_denominators']=int((mask&(target<=0)).sum())
    audit['tiny_price_denominators_lt_1e_8']=int((mask&(target>0)&(target<1e-8)).sum())
    for low,high in zip((0,.1,1,5,20,100),(.1,1,5,20,100,float('inf'))):
        keep=mask&(target>0)&(target>=low)&(target<high);count=int(keep.sum());prefix=f'price_band_{low}_{high}/';audit[prefix+'count']=count
        if count:
            error=(pred-target).abs()[keep];relative=100*error/target[keep]
            audit.update({prefix+'mae':float(error.mean()),prefix+'mean_pct':float(relative.mean()),prefix+'median_pct':float(relative.median()),prefix+'p90_pct':float(relative.quantile(.9))})
    target_code,_=coordinates(data[0][...,:18],valid)
    for base in (0,6,12):
        keep=valid[...,base:base+4].all(-1)
        for j in range(base+1,base+4):
            p=code[...,j][keep];t=target_code[...,j][keep];prefix=f'shape_coordinate_{j}/'
            audit.update({prefix+'prediction_std':float(p.std()),prefix+'target_std':float(t.std()),prefix+'negative_pct':float((p<0).float().mean()*100)})
    audit['negative_predictions']=int(((prediction<0)&valid).sum())
    audit['nonfinite']=int((~torch.isfinite(prediction)&valid).sum())
    if audit['nonfinite']:raise FloatingPointError('Nonfinite capacity audit')
    detail['capacity_audit']=audit;metrics.update({'all_visible/audit/'+k:v for k,v in audit.items()})
    metrics['evaluation_seconds']+=time.perf_counter()-started;model.train();return detail,metrics
