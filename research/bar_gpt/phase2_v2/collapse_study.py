"""Bounded fresh-weight ablations; never resumes or promotes a training run.

Inputs must be frozen production-tokenizer examples, with disjoint session splits.
The future target and 320-coordinate masked MSE remain unchanged in every arm.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import json
import time
from pathlib import Path
import torch
from torch import nn
from .model import Forecaster, future_loss
from ..v5.encoder_v3 import AttentionBlock


class NarrowForecaster(Forecaster):
    def __init__(self, *, stable=False, coordinate_readout=False, content_skip=False):
        nn.Module.__init__(self)
        self.coordinate_readout = coordinate_readout
        self.stable = stable
        self.content_skip = content_skip
        self.identity = nn.Embedding(512, 16)
        if content_skip: nn.init.normal_(self.identity.weight,std=.02)
        self.layers = nn.ModuleList([AttentionBlock(16, 1, 64, 0., 'sdpa', 0.) for _ in range(6)])
        self.norm = nn.LayerNorm(16, elementwise_affine=not stable)
        self.readout = nn.Sequential(nn.Linear(16, 512), nn.GELU())
        self.scalar = nn.Linear(16, 1, bias=False) if coordinate_readout else None
        self.predictor = nn.Sequential(nn.Linear(512, 256), nn.GELU(), nn.Linear(256, 512))

    def forward_visible(self, tokens, metadata, ids):
        content=tokens.index_select(2, ids).transpose(1, 2)
        x = content + self.identity(ids)
        if self.content_skip:
            # Paired zero-content baseline cancels coordinate-only pooled offsets.
            x=torch.cat((x,self.identity(ids).expand(1,-1,-1)),0)
        for layer in self.layers:
            updated = layer(x)
            x = x + .1 * (updated - x) if self.stable else updated
        x = self.norm(x)
        if self.coordinate_readout:
            # Output-only coordinate-aware aggregation; no added input tokens.
            values = self.scalar(x).squeeze(-1)
            if self.content_skip:
                # Preserve observed coordinate content at OUTPUT, not future values.
                values=(values[:-1]-values[-1:]+content[:,:,7].to(values.dtype))*(512/len(ids))
            state = values.new_zeros((len(values), 512)).scatter(1, ids.expand(len(values), -1), values)
        else:
            state = self.readout(x).mean(1)
        return state, self.predictor(state)


def make_model(name):
    if name == 'original': return Forecaster()
    if name == 'narrow': return NarrowForecaster()
    if name == 'stable': return NarrowForecaster(stable=True)
    if name == 'coordinate': return NarrowForecaster(stable=True, coordinate_readout=True)
    if name == 'content': return NarrowForecaster(stable=True, coordinate_readout=True,content_skip=True)
    raise ValueError(name)


def extract(certificate, output):
    """Spread years/sessions and origins; 2025 dev, untouched 2026 final validation."""
    from dataclasses import replace
    import polars as pl
    from .data import load_units, checked_unit, plan_block
    from .tokenizer import load_teacher, SessionTokens
    from .teacher_selection import select_training_teacher
    from ..v5.shards.contract import file_hash
    if output.exists(): raise FileExistsError(output)
    units, release = load_units(release=certificate)
    path, sha, selection = select_training_teacher()
    teacher, proof = load_teacher(path, sha, 'cuda', required_epoch=50)
    candidates = []
    offset = 0
    for root, binding, table in units.groups:
        candidates.append(table.with_row_index('i',offset).select('i','path'))
        offset += table.height
    table=pl.concat(candidates).unique('path',maintain_order=True)
    examples={'train':[], 'dev':[]}; evidence=[]
    seen=set()
    for year in range(2019,2026):
        subset=table.filter(pl.col('path').str.contains('/'+str(year)+'-',literal=True))
        if not subset.height: raise ValueError(f'No year {year}')
        rows=subset.sample(n=min(256,subset.height),shuffle=True,seed=19+year).iter_rows(named=True)
        count=0; wanted=12 if year==2025 else 8
        for row in rows:
            unit=units[int(row['i'])]
            key=tuple(unit.path.parts[-2:])
            if key in seen: continue
            reader=checked_unit(unit)
            plan=next((p for b in unit.blocks if len((p:=plan_block(reader,b[0])).origins)>=193),None)
            if plan is None: continue
            indices=torch.linspace(0,len(plan.origins)-1,4).long().numpy()
            small=replace(plan,origins=plan.origins[indices],endpoints=tuple(e[indices] for e in plan.endpoints),
                targets=plan.targets[indices],origin_us=plan.origin_us[indices])
            session=SessionTokens(reader,teacher,sha,device='cuda',precision='bf16',memory_mib=2048,token_batch=2048)
            session.prepare(small)
            x,meta,y=session.batch(small,0,4)
            split='dev' if year==2025 else 'train'
            examples[split].append((x.cpu(),y.cpu()))
            evidence.append(dict(path=str(unit.path),receipt=reader.receipt['hash'],year=year,
                split=split,origins=small.origin_us.tolist()))
            seen.add(key);count+=1
            del session
            if count==wanted: break
        if count != wanted: raise ValueError(f'Insufficient diverse sessions {year}: {count}')
        print(json.dumps(dict(year=year,sessions=count)),flush=True)
    result=dict(evidence=dict(release=release,teacher=proof,samples=evidence,
        policy='2019-2024 fitting; 2025 development; no 2026 hyperparameter selection',token_batch=2048))
    for split,rows in examples.items():
        result[split+'_x']=torch.cat([r[0] for r in rows])
        result[split+'_y']=torch.cat([r[1] for r in rows])
    torch.save(result,output)
    output.with_suffix('.sha256').write_text(file_hash(output))


@torch.no_grad()
def score(model, x, y, train_mean):
    model.eval()
    result = {}
    for name, ids in [('full', torch.arange(512, device=x.device)),
                      ('fixed_mask', torch.arange(192, device=x.device))]:
        state, p = model.forward_visible(x, None, ids)
        _, zero = model.forward_visible(torch.zeros_like(x), None, ids)
        _, shuffled = model.forward_visible(x.flip(0), None, ids)
        result[name] = dict(mse=float((p-y).square().mean()),
            mean_mse=float((train_mean-y).square().mean()),
            prediction_variance=float(p.var(0, unbiased=False).mean()),
            representation_variance=float(state.var(0, unbiased=False).mean()),
            zero_mse=float((zero-y).square().mean()),
            shuffled_mse=float((shuffled-y).square().mean()))
    model.train()
    return result


def run(dataset, output, steps=1200, arms=('original','narrow','stable','coordinate'), all_targets=False, center=False):
    from ..v5.shards.contract import file_hash
    if output.exists(): raise FileExistsError(output)
    if dataset.with_suffix('.sha256').read_text().strip()!=file_hash(dataset):
        raise ValueError('Frozen study dataset changed')
    data = torch.load(dataset, map_location='cuda', weights_only=False)
    x, y, dx, dy = (data[k].float() for k in ('train_x', 'train_y', 'dev_x', 'dev_y'))
    normalization=None
    if center:
        xm,ym=x.mean(0),y.mean(0)
        normalization=dict(input_mean=xm.cpu(),target_mean=ym.cpu())
        x=x-xm;dx=dx-xm;y=y-ym;dy=dy-ym
    mean = y.mean(0)
    results = dict(dataset=str(dataset), evidence=data['evidence'], steps=steps,
        objective='all future coordinates' if all_targets else '320 dropped future coordinates',
        centering='training examples only' if center else None,arms=[])
    for name in arms:
        torch.manual_seed(41)
        model = make_model(name).cuda()
        opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=.01, fused=True)
        initial = score(model, dx, dy, mean)
        history = []
        torch.cuda.synchronize(); start = time.perf_counter()
        # Same example selections and coordinate masks across arms, independently of init RNG.
        generator = torch.Generator(device='cuda').manual_seed(613)
        for step in range(steps):
            batch = torch.randint(len(x), (min(64, len(x)),), device='cuda', generator=generator)
            ids = torch.randperm(512, device='cuda', generator=generator)[:192].sort().values
            opt.zero_grad(set_to_none=True)
            with torch.autocast('cuda', dtype=torch.bfloat16):
                _, pred = model.forward_visible(x[batch], None, ids)
                loss = (pred.float()-y[batch]).square().mean() if all_targets else future_loss(pred, y[batch], ids)
            loss.backward()
            norm = nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
            if not torch.isfinite(loss): raise FloatingPointError(name)
            opt.step()
            if (step+1) % 300 == 0:
                history.append(dict(step=step+1, loss=float(loss.detach()), dev=score(model, dx, dy, mean)))
        torch.cuda.synchronize()
        arm = dict(name=name, seconds=time.perf_counter()-start, initial=initial,
            train=score(model,x,y,mean),dev=score(model,dx,dy,mean),history=history)
        results['arms'].append(arm)
        output.with_suffix('.progress.json').write_text(json.dumps(results, indent=2))
        torch.save(dict(model=model.state_dict(),name=name,all_targets=all_targets,
            normalization=normalization,dataset_sha256=file_hash(dataset),steps=steps),
            output.with_name(output.stem+'-'+name+'.pt'))
        print(json.dumps(arm), flush=True)
        del model,opt
        torch.cuda.empty_cache()
    output.write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--extract',type=Path,help='Certified release to extract before studying')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',type=int,default=1200)
    parser.add_argument('--arms',nargs='+',choices=('original','narrow','stable','coordinate','content'),
        default=('original','narrow','stable','coordinate'))
    parser.add_argument('--all-targets',action='store_true',help='Explicit experimental future-loss change')
    parser.add_argument('--center',action='store_true',help='Training-only mean centering experiment')
    args=parser.parse_args()
    if not 1 <= args.steps <= 5000: parser.error('Bounded study requires 1..5000 updates')
    if args.extract: extract(args.extract,args.dataset)
    run(args.dataset,args.output,args.steps,args.arms,args.all_targets,args.center)
