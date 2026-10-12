"""Matched closed-loop feedback benchmark; public validation requires all gates."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
os.environ.setdefault('OMP_NUM_THREADS','2')
import argparse, gc, json, subprocess, time, traceback, uuid
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.architecture_benchmark import loss, report, passes
from research.rl_trading.v6.architecture_benchmark_data import FIELDS
from research.rl_trading.v6.feedback_teacher import build
from research.rl_trading.v6.laptop_resources import LaptopGpuPacer
from research.rl_trading.v6.run_architecture_benchmark import write, save, admit_validation
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.rl_trading.v6.transformer_search import selection


def load_shard(receipt, root, data):
    path=Path(receipt['path']).resolve()
    if path.parent!=root.resolve() or file_hash(path)!=receipt['sha256']:
        raise ValueError('Prefix shard path/hash changed')
    shard=torch.load(path,weights_only=False); rows=shard['original_rows'];seq=shard['sequence']
    if shard['keys']!=[data['keys'][i] for i in rows] or len(rows)!=receipt['rows']:
        raise ValueError('Prefix row identity changed')
    for name in ('raw','present','held','multi','multi_present','liquidity','market'):
        if not torch.equal(seq[name][:,-1],data[name][rows]):raise ValueError('Current inputs changed: '+name)
    if not torch.all(seq['max_input_close_us']<seq['clocks_us']):raise ValueError('Prefix cutoff violated')
    return rows,seq


def target_batch(data, rows, device):
    return {k:data[k][rows].to(device) for k in FIELDS}


def sequence_batch(sequence, rows, device):
    return {k:v[rows].to(device) for k,v in sequence.items() if k!='max_input_close_us'}


@torch.no_grad()
def evaluate(model,data,receipts,prefix,device,pacer):
    model.eval();outputs={k:[] for k in ('current','quality','ratio','future','future_quality')};order=[]
    for receipt in receipts:
        rows,sequence=load_shard(receipt,prefix,data)
        for begin in range(0,len(rows),8):
            chosen=rows[begin:begin+8];item=sequence_batch(sequence,slice(begin,begin+8),device)
            base=model.base_sequence(item,pacer)
            pacer.begin_activity();context,_=model.feedback_context(base,item)
            head=model.heads(context,item['held'][:,-1,0]>0)
            future=model.forecast(context,model.forecast_gru,steps=5)
            values=dict(current=head.logits.softmax(-1),quality=head.quality,
                ratio=model.size(context).sigmoid().flatten(),future=future.exp(),future_quality=model.forecast.quality_predictions)
            for name,value in values.items():outputs[name].append(value.cpu().numpy())
            order.extend(chosen);pacer()
        del sequence;gc.collect()
    if sorted(order)!=list(range(len(data['action']))):raise ValueError('Missing/duplicate evaluation rows')
    inverse=np.argsort(order);arrays={k:np.concatenate(v)[inverse] for k,v in outputs.items()}
    return report(data,**arrays),arrays


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,default=Path('D:/TradingML/runtimes/v6-feedback-feature-panel-20261012'))
    parser.add_argument('--group-a',type=Path,default=Path('D:/TradingML/runtimes/v6-enriched-comparison-4-20261012'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--joint-updates',type=int,default=256);parser.add_argument('--head-updates',type=int,default=384)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();runtime=Path('D:/TradingML/runtimes').resolve();out=args.output.resolve()
    if not runtime.is_dir() or not out.is_relative_to(runtime) or (out.exists() and not args.resume):raise ValueError('Fresh runtime or authenticated resume required')
    if not 1<=args.joint_updates<=256 or not 1<=args.head_updates<=384:raise ValueError('Fixed bounded budget required')
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    torch.set_num_threads(2);torch.set_num_interop_threads(2);torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
    torch.backends.cudnn.allow_tf32=False;torch.backends.cuda.matmul.allow_tf32=False
    proof=json.loads((args.prefix/'complete.json').read_text());pm=json.loads((args.prefix/'manifest.json').read_text())
    if proof['manifest_sha256']!=file_hash(args.prefix/'manifest.json') or proof['status']!='prepared' or proof['sealed_labels_read'] or not proof['final_inputs_exact']:
        raise ValueError('Authenticated prefix completion required')
    panel=Path(pm['panel']);pp=json.loads((panel/'complete.json').read_text())
    if file_hash(panel/'complete.json')!=pm['panel_sha256']:raise ValueError('Prefix parent changed')
    a=json.loads((args.group_a/'manifest.json').read_text());ac=json.loads((args.group_a/'complete.json').read_text())
    if len(ac['candidates'])!=4 or not ac['equal_sample_exposure'] or not all(r['reload_exact'] for r in ac['candidates']):raise ValueError('Group A completion required')
    parent=Path(a['parent_runtime'])
    for name,key in (('normalization.json','normalization_sha256'),('channel-normalization.json','channel_normalization_sha256')):
        if file_hash(parent/name)!=a[key]:raise ValueError('Original normalizer changed')
    if file_hash(panel/'normalization.json')!=pm['normalization_sha256']:raise ValueError('Enrichment normalizer changed')
    base=json.loads((parent/'normalization.json').read_text());extra=json.loads((parent/'channel-normalization.json').read_text())
    norm=json.loads((panel/'normalization.json').read_text())
    if file_hash(panel/'train.pt')!=pp['train_sha256']:raise ValueError('Frozen TRAIN changed')
    train=torch.load(panel/'train.pt',weights_only=False)
    receipts=[r for r in proof['shards'] if r['role']=='train'];parts=[];row_order=[]
    for receipt in receipts:
        rows,seq=load_shard(receipt,args.prefix,train);parts.append(seq);row_order.extend(rows)
    if sorted(row_order)!=list(range(128)):raise ValueError('Exact128 TRAIN rows required')
    inverse=np.argsort(row_order);sequence={k:torch.cat([s[k] for s in parts])[inverse] for k in parts[0]};del parts;gc.collect()
    plan=dict(version='v6-enriched-feedback-comparison-v1',prefix_sha256=file_hash(args.prefix/'complete.json'),
        group_a_manifest_sha256=file_hash(args.group_a/'manifest.json'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')},
        train_sha256=pp['train_sha256'],joint_updates=args.joint_updates,head_updates=args.head_updates,
        supervised_exposure=128*(args.joint_updates+args.head_updates),warmup=8,feedback_detached=True,
        gpu_duty=.5,microbatch=8,cpu_threads=2,seed=17,sealed_labels_read=False,validation_exploratory=True,
        candidates=a['candidates'],wandb_project='rl-v6-transformer-search',
        optimizer=dict(name='AdamW',joint_lr=.001,head_lr=.003,weight_decay=.0001,clip_norm=1.),
        vram_reserve_bytes=4*1024**3,host_reserve_bytes=8*1024**3,
        parameters_include_additional_feedback_fuse=True,
        computational_exposure='nine inference decisions per supervised target;128 supervised rows per update')
    out.mkdir(exist_ok=args.resume)
    if args.resume and json.loads((out/'manifest.json').read_text())!=plan:raise ValueError('Resume source/data/budget changed')
    if not args.resume:write(out/'manifest.json',plan)
    if not torch.cuda.is_available():raise RuntimeError('Laptop CUDA required')
    device=torch.device('cuda');pacer=LaptopGpuPacer(device,duty_cycle=.5);pacer.check_reserve()
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    import wandb
    mass=torch.bincount(train['future'][train['future']>=0],minlength=4).float()
    weights=(mass.sum()/mass).sqrt();weights/= (weights*mass).sum()/mass.sum();weights=weights.to(device)
    results=[]
    try:
        for spec in a['candidates']:
            root=out/spec['architecture'];root.mkdir(exist_ok=True)
            if (root/'complete.json').exists():
                result=json.loads((root/'complete.json').read_text())
                if result['manifest_sha256']!=file_hash(out/'manifest.json') or result['checkpoint_sha256']!=file_hash(root/'selected.pt'):raise ValueError('Completed binding changed')
                results.append(result);continue
            torch.manual_seed(17);model=build(spec,base,extra,norm).to(device);start=0;cache=None
            optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
            if (root/'state.json').exists():
                receipt=json.loads((root/'state.json').read_text());path=root/receipt['file']
                if path.parent!=root or receipt['manifest_sha256']!=file_hash(out/'manifest.json') or file_hash(path)!=receipt['sha256']:raise ValueError('State binding changed')
                state=torch.load(path,weights_only=False,map_location='cpu');start=state['update'];model.load_state_dict(state['model'])
                optimizer=torch.optim.AdamW(model.head_parameters() if start>args.joint_updates else model.parameters(),lr=.003 if start>args.joint_updates else .001,weight_decay=.0001)
                optimizer.load_state_dict(state['optimizer'])
            wid=json.loads((root/'wandb.json').read_text())['id'] if (root/'wandb.json').exists() else None
            logger=wandb.init(project=plan['wandb_project'],group=out.name,name=out.name+'-'+spec['architecture'],dir=str(root),mode='online',config=plan,id=wid,resume='allow' if wid else None)
            if logger is None or logger.settings.mode!='online':raise ValueError('Online W&B required')
            write(root/'wandb.json',dict(id=logger.id,url=logger.url));metrics=None
            try:
                for update in range(start,args.joint_updates+args.head_updates):
                    if update==args.joint_updates:optimizer=torch.optim.AdamW(model.head_parameters(),lr=.003,weight_decay=.0001)
                    if update>=args.joint_updates and cache is None:
                        model.eval();chunks=[]
                        with torch.no_grad():
                            for begin in range(0,128,8):chunks.append(model.base_sequence(sequence_batch(sequence,slice(begin,begin+8),device),pacer).cpu())
                        cache=torch.cat(chunks)
                    model.train();optimizer.zero_grad(set_to_none=True);order=np.random.default_rng(17+update).permutation(128);total=0.
                    for begin in range(0,128,8):
                        indices=torch.as_tensor(order[begin:begin+8]);seq=sequence_batch(sequence,indices,device)
                        encoded=cache[indices].to(device) if cache is not None else model.base_sequence(seq,pacer)
                        pacer.begin_activity();context,_=model.feedback_context(encoded,seq)
                        value=loss(model,context,target_batch(train,indices,device),weights)
                        if not torch.isfinite(value):raise RuntimeError('Nonfinite objective')
                        (value/16).backward();total+=float(value.detach())/16;pacer()
                    pacer.begin_activity();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step();pacer()
                    if (update+1)%16==0 or update+1==args.joint_updates+args.head_updates:
                        metrics,arrays=evaluate(model,train,receipts,args.prefix,device,pacer)
                        record=dict(update=update+1,sample_exposure=(update+1)*128,loss=total,train=metrics,underfit_passed=passes(metrics))
                        logger.log(flatten(record),step=update+1)
                        with (root/'metrics.jsonl').open('a') as stream:stream.write(json.dumps(record)+'\n')
                        path=root/f'state-{update+1:05d}-{uuid.uuid4().hex}.pt';save(path,dict(update=update+1,model=model.state_dict(),optimizer=optimizer.state_dict()))
                        write(root/'state.json',dict(file=path.name,sha256=file_hash(path),manifest_sha256=file_hash(out/'manifest.json')))
                        write(out/'progress.json',dict(phase='training',architecture=spec['architecture'],update=update+1,completed=len(results),queued=3-len(results),active=1,entry_f1=metrics['current']['ENTRY']['f1'],gate_passed=passes(metrics)))
                save(root/'selected.pt',model.state_dict());reloaded=build(spec,base,extra,norm).to(device)
                reloaded.load_state_dict(torch.load(root/'selected.pt',weights_only=True,map_location=device));reloaded.head_parameters()
                replay,replay_arrays=evaluate(reloaded,train,receipts,args.prefix,device,pacer)
                exact=replay==metrics and all(np.array_equal(arrays[k],replay_arrays[k]) for k in arrays)
                if not exact:raise ValueError('Exact checkpoint replay failed')
                admitted=passes(replay);write(root/'overfit-gate.json',dict(passed=admitted,reload_exact=exact,metrics=replay));validation=None
                if admitted:
                    admit_validation(replay,exact)
                    if file_hash(panel/'validation.pt')!=pp['validation_sha256']:raise ValueError('Validation changed')
                    val=torch.load(panel/'validation.pt',weights_only=False)
                    validation,predictions=evaluate(reloaded,val,[r for r in proof['shards'] if r['role']=='validation'],args.prefix,device,pacer)
                    write(root/'validation-metrics.json',validation);np.savez_compressed(root/'validation-predictions.npz',**predictions)
                    logger.log(flatten(validation,'validation'),step=args.joint_updates+args.head_updates+1);del val
                result=dict(architecture=spec['architecture'],sample_exposure=plan['supervised_exposure'],overfit_passed=admitted,reload_exact=exact,train=replay,validation=validation,
                    checkpoint_sha256=file_hash(root/'selected.pt'),manifest_sha256=file_hash(out/'manifest.json'),wandb_url=logger.url)
                write(root/'complete.json',result);results.append(result)
            finally:logger.finish()
            del model,reloaded,optimizer,cache;gc.collect();torch.cuda.empty_cache()
            write(out/'results.json',dict(candidates=results,sealed_labels_read=False));write(out/'selection.json',selection(results))
        write(out/'complete.json',dict(candidates=results,equal_sample_exposure=len({r['sample_exposure'] for r in results})==1,sealed_labels_read=False,validation_is_exploratory=True))
        write(out/'progress.json',dict(phase='completed',active=0,completed=4,failed=sum(not r['overfit_passed'] for r in results)))
    except BaseException:
        write(out/'failure.json',dict(traceback=traceback.format_exc()));raise


if __name__=='__main__':main()
