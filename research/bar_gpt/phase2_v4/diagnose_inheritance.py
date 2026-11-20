"""Compare frozen Phase1 reconstruction and Phase2 forecasts on matched origins."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',POLARS_MAX_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
from .run_train import arguments
from .data import load_units,batches
from .model import Forecaster,Config,Phase1Decoder
from .tokenizer import load_teacher
from .metrics import Metrics,ValidationMetrics
from ..phase2_v2.device_pipeline import device_batches
from ..phase2_v2.train import load_checkpoint,autocast,lease
from ..phase1_v8.runtime import preflight
from ..phase1_v11.transforms import references,decode,inverse
from ..v5.shards.contract import file_hash,digest,write_json


FIELDS=['count','phase1_signed_pct','phase1_absolute_pct','phase1_raw_mae','phase1_below_actual',
        'phase2_signed_pct','phase2_absolute_pct','phase2_raw_mae','phase2_predicted_down',
        'actual_down','actual_up','actual_flat','phase1_squared_pct','phase2_squared_pct','cross_product_pct',
        'phase2_predicted_return_pct','actual_return_pct']


def paired_sums(reconstructed,forecast,target):
    ix=[3,9,15];r=reconstructed[:,-1,ix].double().exp();p=forecast[:,0,ix].double().exp()
    last=target['last_raw'][:,ix].double();y=target['raw'][:,0,ix].double()
    mask=target['last_valid'][:,ix]&target['valid'][:,0,ix]&(last>0)&(y>0)
    e1=100*(r/torch.where(mask,last,1)-1);e2=100*(p/torch.where(mask,y,1)-1)
    values=torch.stack((torch.ones_like(y),e1,e1.abs(),(r-last).abs(),(r<last).double(),
        e2,e2.abs(),(p-y).abs(),(p<last).double(),(y<last).double(),(y>last).double(),(y==last).double(),
        e1.square(),e2.square(),e1*e2,100*(p/torch.where(mask,last,1)-1),100*(y/torch.where(mask,last,1)-1)),-1)
    return torch.where(mask[...,None],values,0).sum(0)


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);own=p.parse_args()
    argv=json.loads((own.run/'process.json').read_text())['command'];argv=argv[argv.index('research.bar_gpt.phase2_v4.run_train')+1:]
    for flag in ('--resume','--forecast-audit'):
        if flag in argv:i=argv.index(flag);del argv[i:i+2]
    if '--cuda-step-replay' in argv:argv.remove('--cuda-step-replay')
    for flag,value in (('--mode','profile'),('--output',str(own.output)),('--source-revision',own.source_revision)):argv[argv.index(flag)+1]=value
    a=arguments(argv);preflight(a);torch.set_num_threads(a.threads)
    checkpoint=own.run/'checkpoint_last.pt';saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
    saved=load_checkpoint(checkpoint,saved['contract']);checkpoint_hash=file_hash(checkpoint)
    teacher,proof,_=load_teacher(a.teacher_checkpoint,a.teacher_sha256,a.phase1_calibration,a.device)
    decoder=Phase1Decoder().to(a.device).eval()
    decoder.initialize_phase1(torch.load(a.teacher_checkpoint,map_location='cpu',weights_only=False)['model'])
    model=Forecaster(Config(**saved['contract']['model'])).to(a.device).eval();model.load_state_dict(saved['model'])
    units,dataset=load_units(release=a.dataset_release)
    if digest(dataset)!=digest(saved['contract']['dataset']):raise ValueError('Dataset differs')
    moments=saved['training_metrics']['moments'].to(a.device);mean=moments[2]/moments[4].clamp_min(1)
    metric=Metrics(a.device);native=ValidationMetrics(a.device);paired=torch.zeros(3,len(FIELDS),device=a.device,dtype=torch.float64)
    future_reference=torch.zeros(9,3,device=a.device,dtype=torch.float64)
    oracle_sums=torch.zeros(18,3,device=a.device,dtype=torch.float64)
    source=None;started=time.perf_counter()
    try:
        with lease(a.output),torch.no_grad():
            source=device_batches(batches(units,1,teacher,proof['sha256'],a),a.device,a.gpu_prefetch)
            for tokens,meta,target,_,_,_ in source:
                if (a.output/'STOP').exists():raise RuntimeError('Audit stopped; incomplete')
                with autocast(a):z,pred=model(tokens,meta)
                rec=decoder(tokens[:,7]);paired+=paired_sums(rec,pred,target)
                detail=model.decoder.details(z)
                true_ref,rv=references(target['logs'],target['valid'],teacher.reference_mean)
                delta=detail['references'].double()-true_ref.double();delta[:,0::3]=100*torch.expm1(delta[:,0::3])
                future_reference+=torch.stack((rv.double(),torch.where(rv,delta,0),torch.where(rv,delta.abs(),0)),-1).sum(0)
                oracle=inverse(decode(detail['code'],true_ref));raw_p=inverse(pred);valid=target['valid']
                oracle_sums+=torch.stack((valid.double(),torch.where(valid,(raw_p-target['raw']).abs(),0),
                                        torch.where(valid,(oracle-target['raw']).abs(),0)),-1).sum((0,1))
                if not torch.isfinite(rec).all() or not torch.isfinite(pred).all():raise FloatingPointError('Nonfinite')
                metric.add(pred,target);native.add(pred,target,mean)
                write_json(a.output/'status.json',dict(state='running',origins=metric.count,seconds=time.perf_counter()-started))
            result=dict(metric.result(),**native.result());native.forecast.moves.save(a.output,saved['step'])
            if metric.count!=708600:raise ValueError('Unexpected certified validation origin count')
            if file_hash(checkpoint)!=checkpoint_hash:raise ValueError('Checkpoint changed during audit')
            write_json(a.output/'results.json',dict(complete=True,step=saved['step'],source=a.source_revision,
                checkpoint_sha256=checkpoint_hash,teacher=proof,dataset_hash=digest(dataset),seconds=time.perf_counter()-started,
                scope='Same708600 Phase2 validation origins. Frozen Phase1 decoder reconstructs last historical window from tokens[:,7]; compare last reconstructed close to last observed close. Phase2 forecasts compare first future close to its actual future target. Correlation is diagnostic, not causal attribution.',
                reference_diagnostic_scope='True future median substitution is ORACLE ONLY. Phase2 has no separate median-reference supervision; reference and code may compensate, so oracle improvement/worsening is not a uniquely identified reference-head defect.',
                future_reference_fields=['count','signed_error_sum_price_pct_activity_log','absolute_error_sum_price_pct_activity_log'],
                future_reference=future_reference.cpu().tolist(),oracle_raw_fields=['count','normal_absolute_error_sum','oracle_absolute_error_sum'],oracle_raw=oracle_sums.cpu().tolist(),
                fields=FIELDS,channels=['trade_close','bid_close','ask_close'],sums=paired.cpu().tolist(),forecast=result))
            write_json(a.output/'status.json',dict(state='complete',origins=metric.count,seconds=time.perf_counter()-started))
    finally:
        if source:source.close()
        native.native.pool.shutdown(wait=True)


if __name__=='__main__':main()
