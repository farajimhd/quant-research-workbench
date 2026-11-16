"""Bounded real-shard labels/inverse audit; CPU/read-only unless explicit profile."""
import numpy as np
import torch
from ..phase2_v2.data import checked_unit,plan_block
from ..phase2_v2.train import autocast,optimizer_for
from .targets import from_raw,reconstruct,loss
from .data import BarSessionTokens


def bounded_check(units,teacher,proof,args,*,model_type,model_config):
    reports=[]
    model=model_type(model_config()).to(args.device) if args.mode=='profile' else None
    opt=optimizer_for(model,args) if model else None
    for unit in units:
        reader=checked_unit(unit);session=None
        for ordinal,split,liquid,selection in unit.blocks:
            plan=plan_block(reader,ordinal)
            if not len(plan.origins):
                reports.append(dict(path=str(unit.path),block=ordinal,eligible=0,report=plan.report))
                if len(reports)>=args.max_blocks:break
                continue
            n=min(args.profile_origins,len(plan.origins))
            pool=reader.view(0);offsets=np.arange(63,-1,-1)
            fi=plan.targets[:n,None]-offsets;pi=plan.endpoints[0][:n,-1,None]-offsets
            target=from_raw(torch.from_numpy(pool['values'][fi].copy()),torch.from_numpy(pool['valid'][fi].copy()).bool(),
                torch.from_numpy(pool['values'][pi].copy()),torch.from_numpy(pool['valid'][pi].copy()).bool())
            perfect=torch.cat((target['bars'].flatten(1),target['references']),1)
            recovered,refs=reconstruct(perfect,target['origin_log_reference'])
            torch.testing.assert_close(recovered[target['valid']],target['raw'].double()[target['valid']],rtol=1e-5,atol=2e-5)
            rm=target['reference_valid']
            torch.testing.assert_close(refs[rm],target['actual_reference'][rm],rtol=1e-5,atol=2e-5)
            report=dict(path=str(unit.path),receipt=reader.receipt['hash'],block=ordinal,eligible=len(plan.origins),
                audited=n,target_shape=[n,64,18],references_shape=[n,7],roundtrip=True,
                first_origin_us=int(plan.origin_us[0]),last_target_close_us=int(pool['end_us'][plan.targets[n-1]]))
            if model is not None:
                if session is None:session=BarSessionTokens(reader,teacher,proof['sha256'],device=args.device,
                    precision=args.precision,memory_mib=args.cache_mib,token_batch=args.token_batch)
                session.prepare(plan);tokens,meta,target=session.batch(plan,0,n)
                for _ in range(args.profile_steps):
                    opt.zero_grad(set_to_none=True)
                    with autocast(args):value=loss(model(tokens,None)[1],target)
                    value.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);opt.step()
                report['profile_loss']=float(value.detach())
            reports.append(report)
            if len(reports)>=args.max_blocks:break
        if len(reports)>=args.max_blocks:break
    if not any(r.get('audited',0)>0 for r in reports):raise ValueError('No eligible future targets in bounded sample')
    return dict(passed=True,mode=args.mode,blocks=reports,scope='bounded label/inverse audit, not full-data qualification')
