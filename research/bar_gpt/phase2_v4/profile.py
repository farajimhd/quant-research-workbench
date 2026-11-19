"""Real certified pipeline plus disposable compute/overfit gates; no W&B run."""
from dataclasses import replace
import copy,time
import torch
from ..phase2_v2.benchmark import sample_units
from ..phase2_v2.data import checked_unit
from .data import load_units
from itertools import islice
from ..phase2_v2.train import optimizer_for,autocast
from ..phase2_v2.device_pipeline import device_batches
from ..v5.shards.contract import write_json
from .tokenizer import SessionTokens
from .data import batches
from .cuda_step import CudaStep
from .metrics import Metrics,ValidationMetrics
from .model import Forecaster


@torch.no_grad()
def tokenizer_parity(unit,teacher,proof,args):
    """Check optimized inputs against the standalone phase1 preprocessing path."""
    from ..phase1_v6.model import Transform
    from ..v5.transforms_v1 import BarInputTransformV1
    from ..v5.phase2_ar_shards.contract import FRAMES
    from .tokenizer import raw_logs
    session=SessionTokens(checked_unit(unit),teacher,proof['sha256'],device=args.device,
        precision=args.precision,memory_mib=args.cache_mib,token_batch=args.token_batch)
    result=[]
    for frame,(values,valid,clocks) in enumerate(session.pools):
        ids=torch.arange(63,min(63+args.token_batch,len(values)),device=args.device)
        if not len(ids):raise AssertionError('Missing phase1 parity frame')
        gather=ids[:,None]-session.positions.flip(0)
        raw=values[gather];mask=valid[gather].bool()
        standard=BarInputTransformV1().to(args.device)(raw,mask,clocks[gather],FRAMES[frame])
        standard.values[...,:18]=Transform.logs(raw,mask).float()
        time,tm=session.window_time(frame,gather)
        torch.testing.assert_close(raw_logs(raw,mask),standard.values[...,:18],rtol=0,atol=0)
        torch.testing.assert_close(time,standard.values[...,18:],rtol=0,atol=0)
        assert torch.equal(tm,standard.valid[...,18:])
        expected=teacher(standard.values,standard.valid,session.positions.expand(len(ids),-1))
        session.ensure(frame,ids.cpu().numpy())
        torch.testing.assert_close(session.bank[frame][ids],expected,rtol=1e-5,atol=1e-6)
        result.append(dict(frame_us=FRAMES[frame],windows=len(ids),max_embedding_error=float((session.bank[frame][ids]-expected).abs().max())))
    return result


def profile(model,teacher,proof,args,*,model_factory=Forecaster):
    all_units,_=sample_units(args.dataset_release,args.max_blocks)
    released,_=load_units(release=args.dataset_release)
    units=[u for _,u in islice(released.iter_from(0),args.max_blocks)]
    if not units:raise ValueError('No training profile units')
    original=copy.deepcopy(model.state_dict());report=[]
    source=device_batches(batches(units,0,teacher,proof['sha256'],args),args.device,args.gpu_prefetch)
    started=time.perf_counter();first=None;count=0;optimizer=optimizer_for(model,args);replay=None;training_metrics=Metrics(args.device)
    try:
        for tokens,ages,target,*_ in source:
            if first is None:first=(tokens,ages,target);startup=time.perf_counter()-started
            if args.device=='cuda' and len(tokens)==args.batch_size:
                if replay is None:replay=CudaStep(model,args.batch_size,metrics=training_metrics)
                p,loss,norm=replay(tokens,ages,target)
                training_metrics.count+=len(tokens)
            else:
                optimizer.zero_grad(set_to_none=True)
                with autocast(args):p=model(tokens,ages)[1]
                loss=model.training_loss(p,target);loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                training_metrics.add(p,target)
            if not (torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Profile gradient failure')
            optimizer.step();count+=len(tokens)
        if args.device=='cuda':torch.cuda.synchronize()
        report.append(dict(kind='complete_pipeline',origins=count,seconds=time.perf_counter()-started,startup_seconds=startup,
            origins_per_second=count/(time.perf_counter()-started),batch=args.batch_size,prepare_workers=args.prepare_workers))
    finally:source.close()
    write_json(args.output/'profile-partial.json',report);print(report[-1],flush=True)
    if first is None:raise ValueError('No eligible profile origins')
    tokens,ages,target=first
    config=model.config;del replay,optimizer,p,loss,norm,model
    for batch in (256,512,1024,2048,4096,8192):
        model=model_factory(config).to(args.device);model.load_state_dict(original);model.train();opt=optimizer_for(model,args)
        indices=torch.arange(batch,device=tokens.device)%len(tokens)
        x=tokens[indices];a=ages[indices];t={k:v[indices] for k,v in target.items()};graph=CudaStep(model,batch)
        started=time.perf_counter()
        for _ in range(args.profile_steps):
            _,l,norm=graph(x,a,t)
            if not (torch.isfinite(l)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite compute sweep')
            opt.step()
        torch.cuda.synchronize();seconds=time.perf_counter()-started
        report.append(dict(kind='cached_compute',batch=batch,repeated_rows=batch>len(tokens),steps=args.profile_steps,seconds=seconds,origins_per_second=batch*args.profile_steps/seconds,gpu_reserved_mib=torch.cuda.memory_reserved()/2**20))
        write_json(args.output/'profile-partial.json',report);print(report[-1],flush=True)
        del graph,opt,model
    model=model_factory(config).to(args.device);model.load_state_dict(original);model.train();opt=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,fused=args.device=='cuda')
    n=min(32,len(tokens));x=tokens[:n];a=ages[:n];t={k:v[:n] for k,v in target.items()};curve=[]
    for step in range(401):
        opt.zero_grad(set_to_none=True)
        with autocast(args):p=model(x,a)[1]
        loss=model.training_loss(p,t)
        if step%100==0:curve.append(dict(step=step,loss=float(loss.detach())))
        if step<400:loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);opt.step()
    if not curve[-1]['loss']<curve[0]['loss']*.7:raise AssertionError('Tiny overfit failed to reduce objective30%')
    model.eval();native=ValidationMetrics(args.device)
    with torch.no_grad(),autocast(args):predicted=model(x,a)[1]
    native.add(predicted,t,t['logs'].mean(0));native_result=native.result()
    report.append(dict(kind='tiny_overfit',examples=n,curve=curve))
    # Exercise the exact full-pass/checkpoint/validation path on a bounded set.
    # This is a separate disposable qualification, never a claimed corpus pass.
    validation_units=[u for u in all_units if any(b[1]==1 for b in u.blocks)]
    if not validation_units:
        more,_=sample_units(args.dataset_release,64)
        validation_units=[u for u in more if any(b[1]==1 for b in u.blocks)]
    if not validation_units:raise ValueError('Qualification requires a certified validation publication')
    from types import SimpleNamespace
    from .train import train
    from ..phase2_v2.train import load_checkpoint
    class Reporter:
        def update(self,**kw):pass
        def log(self,metrics,step):pass
    qa=SimpleNamespace(**vars(args));qa.output=args.output/'integration';qa.output.mkdir();qa.epochs=1;qa.resume=None;qa.validation_every=10000
    qa.cuda_step_replay=True;qa.seed=args.seed;qa.batch_size=256
    model=model_factory(config).to(args.device);model.load_state_dict(original);model.train()
    contract=dict(qualification=True,teacher=proof['sha256'],source=args.source_revision)
    integration=train(model,teacher,proof['sha256'],[units[0],validation_units[0]],contract,qa,Reporter())
    saved=load_checkpoint(qa.output/'checkpoint_last.pt',contract)
    if integration!='complete' or saved['epoch']!=1 or not saved['history'][-1]['pass_complete']:raise AssertionError('Runnable integration failed')
    report.append(dict(kind='bounded_full_pass_integration',state=integration,step=saved['step'],validation_loss=saved['history'][-1]['validation']['loss']))
    parity=tokenizer_parity(units[0],teacher,proof,args)
    result=dict(passed=True,measurements=report,teacher=proof,native=native_result,tokenizer_parity=parity,scope='Bounded training-publication qualification, not full-corpus generalization')
    write_json(args.output/'profile.json',result);print({k:v for k,v in result.items() if k not in ('native','teacher')},flush=True)
    return result


