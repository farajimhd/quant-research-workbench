"""Fresh ordered-window residual predictor; training-only standalone entry labels."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import argparse
import json
import subprocess
import time
import numpy as np
import torch
from torch import nn
from research.rl_trading.v7.experiments import entry_net as previous
from research.rl_trading.v7.experiments.temporal_data import TemporalDataset
shared=previous.shared
baseline=previous.baseline
causal_extra=previous.causal_extra
tensors=previous.tensors
evaluate=previous.evaluate
save_epoch=previous.save_epoch
read_epoch=previous.read_epoch
publish=previous.publish


def sources():
    return dict(previous.sources(),temporal_net=shared.sha(Path(__file__)),
                temporal_data=shared.sha(Path(__file__).with_name('temporal_data.py')))


def layer():
    return nn.TransformerEncoderLayer(32,4,64,dropout=.1,activation='gelu',batch_first=True,norm_first=True)


class TemporalNet(nn.Module):
    def __init__(self,mean,std):
        super().__init__()
        self.register_buffer('mean',torch.as_tensor(mean,dtype=torch.float32))
        self.register_buffer('std',torch.as_tensor(std,dtype=torch.float32))
        self.bar=nn.Linear(14,32)
        self.position=nn.Parameter(torch.randn(60,32)*.02)
        self.timeframe=nn.Parameter(torch.randn(7,32)*.02)
        self.cls=nn.Parameter(torch.randn(1,1,32)*.02)
        self.window=layer()
        self.cost=nn.Linear(14,32)
        self.fusion=layer()
        self.account_token=nn.Linear(8,32)
        self.market=layer()
        self.norm=nn.LayerNorm(32)
        self.head=nn.Sequential(nn.Linear(54,64),nn.SiLU(),nn.Linear(64,1))
        nn.init.zeros_(self.head[-1].weight);nn.init.zeros_(self.head[-1].bias)

    def encode(self,seq,features,account,candidate_valid):
        b=len(seq)
        x=self.bar(seq)+self.position[None,None,None]+self.timeframe[None,None,:,None]
        valid=seq[...,7:].bool().any(-1).reshape(-1,60)
        x=x.reshape(-1,60,32)
        # The summary token retains timeframe identity even when every bar in
        # that window is missing and therefore masked from attention.
        cls=self.cls.expand(len(x),-1,-1)+self.timeframe.repeat(b*5,1)[:,None]
        x=torch.cat((cls,x),1)
        padding=~torch.cat((torch.ones((len(x),1),dtype=torch.bool,device=x.device),valid),1)
        windows=self.window(x,src_key_padding_mask=padding)[:,0].reshape(b*5,7,32)
        extra=(causal_extra(features,account)-self.mean)/self.std
        cost=self.cost(extra[...,:14]).reshape(b*5,1,32)
        fused=self.fusion(torch.cat((windows,cost),1)).mean(1).reshape(b,5,32)
        tokens=torch.cat((fused,self.account_token((account-self.mean[-8:])/self.std[-8:])[:,None]),1)
        padding=~torch.cat((candidate_valid,torch.ones((b,1),dtype=torch.bool,device=seq.device)),1)
        return self.norm(self.market(tokens,src_key_padding_mask=padding))[:,:5],extra

    def forward(self,seq,features,account,holding,candidate_valid):
        tokens,extra=self.encode(seq,features,account,candidate_valid)
        result=baseline(features,account)+10*self.head(torch.cat((tokens,extra),-1)).squeeze(-1)
        if not torch.isfinite(result).all():raise ValueError('Nonfinite temporal prediction')
        return result


def temporal_receipts(data):
    if len(data.days)!=150:raise ValueError('Require all150 TRAINING temporal days')
    receipts={}
    for day in data.days:
        data.load(day)
        path=data.root/day/'complete.json'
        receipt=json.loads(path.read_text())
        if receipt.get('parity_exact') is not True or receipt['parity_scalar_count']!=receipt['admitted_candidates']*98:
            raise ValueError('Missing exact temporal summary parity')
        receipts[day]=dict(receipt_sha256=shared.sha(path),files=receipt['files'])
    return receipts


def materialize(data,labels,temporal,receipts,progress=None):
    obs,y,mask,days=previous.materialize(data,labels,progress)
    count=len(days)
    size=count*5*7*60*14*4
    if size>1.2*2**30:raise MemoryError('Temporal materialization exceeds1.2GiB sequence budget')
    seq=np.empty((count,5,7,60,14),np.float32)
    cursor=0
    for day in data.days:
        if shared.sha(temporal.root/day/'complete.json')!=receipts[day]['receipt_sha256']:
            raise ValueError('Temporal receipt changed during materialization')
        arrays=temporal.load(day)
        n=len(arrays['rows'])
        if not np.array_equal(arrays['rows'],np.arange(0,len(data.load(day)['seconds']),10)):
            raise ValueError('Temporal label grid mismatch')
        seq[cursor:cursor+n]=arrays['seq'];cursor+=n
    if cursor!=count:raise ValueError('Temporal sample count mismatch')
    obs['seq']=seq
    return obs,y,mask,days


class Scorer:
    row_step=10
    def __init__(self,model,binding,temporal,device):
        self.model,self.binding,self.temporal,self.device=model,binding,temporal,device

    def for_session(self,session):
        day=session.day
        if day not in self.temporal.days:raise ValueError('Only approved TRAINING scorer dates')
        expected=self.binding['contract']['temporal_receipts'][day]
        if shared.sha(self.temporal.root/day/'complete.json')!=expected['receipt_sha256']:
            raise ValueError('Temporal day differs from trained receipt')
        arrays=self.temporal.load(day)
        snapshots=causal_snapshots(self.temporal.predictive,day)
        rows=arrays['rows'];seconds=snapshots['seconds'][rows]
        names=self.temporal.predictive.source.items[day]['selected']
        if (session.tickers!=names or not np.array_equal(session.selection[seconds],snapshots['selection'][rows]) or
                not np.array_equal(session.features[seconds],snapshots['features'][rows,:,:98])):
            raise ValueError('Replay session temporal market/admission parity mismatch')
        return BoundScorer(self,arrays['seq'],snapshots,rows,seconds)

    def score(self,observations):
        raise ValueError('Call for_session before temporal scoring')


def causal_snapshots(predictive,day):
    """Replay opens causal arrays only; neither forward-return nor entry labels."""
    identity=predictive._identity(day)
    root=predictive.root/day
    receipt=json.loads((root/'complete.json').read_text())
    if receipt['identity']!=identity:raise ValueError('Causal snapshot identity mismatch')
    arrays={}
    for key in ('features','account','candidate_valid','selection','seconds'):
        path=root/(key+'.npy')
        if shared.sha(path)!=receipt['files'][path.name]:raise ValueError('Causal snapshot hash mismatch')
        arrays[key]=np.load(path,mmap_mode='r',allow_pickle=False)
        if not np.isfinite(arrays[key]).all():raise ValueError('Nonfinite causal snapshot')
    return arrays


class BoundScorer:
    row_step=10
    def __init__(self,parent,seq,snapshots,rows,seconds):
        self.parent,self.seq,self.snapshots,self.rows=parent,seq,snapshots,rows
        self.clock_index={int(t):i for i,t in enumerate(seconds)}
        self.binding=parent.binding

    def score(self,observations):
        obs=shared.observation_batch(observations)
        clocks=np.rint(obs['account'][:,3].astype(np.float64)*57600).astype(np.int64)
        if any(int(t) not in self.clock_index for t in clocks):raise ValueError('Temporal score clock outside bound sampled grid')
        ix=np.asarray([self.clock_index[int(t)] for t in clocks]);rows=self.rows[ix]
        if (not np.array_equal(obs['features'][...,:98],self.snapshots['features'][rows,:,:98]) or
                not np.array_equal(obs['candidate_valid'],self.snapshots['candidate_valid'][rows]) or
                not np.array_equal(obs['account'][:,3],self.snapshots['account'][rows,3])):
            raise ValueError('Temporal causal clock/features/admission mismatch')
        results=[]
        with torch.no_grad():
            for begin in range(0,len(ix),16):
                chunk=np.arange(begin,min(begin+16,len(ix)))
                batch=tensors(obs,chunk,self.parent.device)
                batch['seq']=torch.as_tensor(np.asarray(self.seq[ix[chunk]]),device=self.parent.device)
                results.append(self.parent.model(**batch).cpu().numpy())
        return np.where(obs['candidate_valid'],np.concatenate(results),-np.inf)


def load_bundle(root,dataset,forecast=None,*,device='cpu'):
    root=Path(root).resolve();manifest=json.loads((root/'manifest.json').read_text())
    c,h=manifest['contract'],manifest['contract_hash']
    if (shared.digest(c)!=h or c['source']!=sources() or c['base_source']!=shared.source_identity() or
            c['training_binding']!=dataset.binding or c['risk']!=shared.Config().manifest() or
            c['fit_days']!=dataset.days[:120] or c['development_days']!=dataset.days[120:] or
            len(dataset.days)!=150 or c['validation_access'] is not False or c['forecast_reuse'] is not False):
        raise ValueError('Temporal-net source/data/split contract mismatch')
    temporal=TemporalDataset(dataset.root,c['roots']['snapshots'],c['roots']['temporal'])
    if temporal.binding!=c['temporal_binding']:raise ValueError('Temporal recipe changed')
    pointer=json.loads((root/'best.json').read_text())
    state=read_epoch(root,pointer['epoch'],h)
    if pointer!=dict(epoch=state['epoch'],sha256=shared.sha(root/f"epoch-{state['epoch']:03d}.pt"),contract_hash=h):
        raise ValueError('Best pointer mismatch')
    model=TemporalNet(np.zeros(22),np.ones(22)).to(device)
    model.load_state_dict(state['model']);model.requires_grad_(False).eval()
    if torch.any(model.std<=0):raise ValueError('Invalid temporal scaler')
    return Scorer(model,dict(root=str(root),contract=c,contract_hash=h,checkpoint=pointer),temporal,device)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', default=shared.DEFAULT_DEST)
    p.add_argument('--snapshots', default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--labels', default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    p.add_argument('--temporal', default='D:/TradingML/runtimes/rl-trading/v7/temporal-data-v1')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--epochs', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=16)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args(argv)
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not 1 <= args.batch_size <= 32 or not 1 <= args.epochs <= 20:
        raise ValueError('Runtime output and bounded epochs/batch required')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable; select CPU explicitly')
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(1917)
    temporal = TemporalDataset(args.source, args.snapshots, args.temporal)
    data = temporal.predictive
    receipts = temporal_receipts(temporal)
    labels = shared.EntryLabels(data, args.labels)
    contract = dict(source=sources(), base_source=shared.source_identity(), training_binding=data.source.binding,
        snapshots_binding=data.binding, label_contract=labels.contract, label_receipts=labels.receipts,
        label_manifest_sha256=shared.sha(labels.root/'manifest.json'),
        temporal_binding=temporal.binding, temporal_receipts=receipts, forecast_reuse=False,
        forecast_argument='ignored compatibility argument at scoring only', risk=shared.Config().manifest(),
        fit_days=data.fit_days, development_days=data.development_days, row_step=10,
        recipe='fresh-temporal-width32-fourhead-window-fusion-market-residual-v1', seed=1917,
        encoder_lr=1e-4, head_lr=1e-3, weight_decay=.01, loss='MSE(net dollars/10)', dropout=.1, window_position=60, summary_token=False,
        scaler='fit-only accepted cost14/account8; nearconstant threshold1e-12',
        selection='minimum internal30 development MSE; epoch0 baseline included',
        epochs=args.epochs, batch_size=args.batch_size, device=args.device,
        torch_version=torch.__version__, numpy_version=np.__version__, validation_access=False,
        roots={k: str(Path(getattr(args, k)).resolve()) for k in ('source','snapshots','labels','temporal','output')},
        evidence='standalone overlapping entry outcomes, NOT persistent-account profit')
    h = shared.digest(contract)
    if args.resume:
        previous = json.loads((root/'manifest.json').read_text())
        if previous['contract'] != contract or previous['contract_hash'] != h:
            raise ValueError('Exact resume contract mismatch')
    else:
        root.mkdir(parents=True, exist_ok=False)
        shared.write_json(root/'manifest.json', dict(contract=contract, contract_hash=h,
            git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                cwd=Path(__file__).resolve().parents[4], text=True).strip()))
    started = time.monotonic()
    try:
        shared.write_json(root/'status.json', dict(status='materializing', completed=0, queued=150, failed=0))
        obs, y, mask, days = materialize(data, labels, temporal, receipts, lambda completed: shared.write_json(root/'status.json',
            dict(status='materializing', completed=completed, queued=150-completed, failed=0)))
        fit_rows = np.flatnonzero(np.isin(days, data.fit_days))
        dev_rows = np.flatnonzero(np.isin(days, data.development_days))
        if not mask[fit_rows].any() or not mask[dev_rows].any():
            raise ValueError('Empty internal partition')
        extra = causal_extra(obs['features'], obs['account'])[fit_rows][mask[fit_rows]].astype(np.float64)
        mean, std = extra.mean(0), extra.std(0)
        std[std <= 1e-12*np.maximum(1., abs(mean))] = 1
        model = TemporalNet(mean, std).to(args.device)
        optimizer = torch.optim.AdamW([dict(params=[v for name,v in model.named_parameters() if not name.startswith('head.')], lr=1e-4),
                                      dict(params=model.head.parameters(), lr=1e-3)], weight_decay=.01)
        generator = torch.Generator().manual_seed(1917)
        fit_mean = float(y[fit_rows][mask[fit_rows]].astype(np.float64).mean())
        dev_y = y[dev_rows][mask[dev_rows]].astype(np.float64)
        base = baseline(obs['features'][dev_rows], obs['account'][dev_rows])[mask[dev_rows]].astype(np.float64)
        baselines = dict(fit_mean=fit_mean, constant_mse=float(np.mean((dev_y-fit_mean)**2)),
                         current_quote_mse=float(np.mean((dev_y-base)**2)))
        metrics = [dict(epoch=0, development=evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size))]
        best, best_epoch, start = metrics[0]['development']['mse'], 0, 1
        def state(epoch):
            return dict(epoch=epoch, contract_hash=h, model=model.state_dict(), optimizer=optimizer.state_dict(),
                        rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all() if args.device=='cuda' else [],
                        shuffle=generator.get_state(), best=best, best_epoch=best_epoch, metrics=metrics, baselines=baselines)
        if args.resume:
            # Sealed epochs are authoritative even if publication was interrupted.
            epochs = sorted(int(path.name.split('-')[1].split('.')[0]) for path in root.glob('epoch-*.complete.json'))
            if not epochs or epochs != list(range(max(epochs)+1)):
                raise ValueError('Noncontiguous committed epochs')
            for path in root.glob('epoch-*.pt'):
                if not path.with_suffix('.complete.json').exists():
                    quarantine = root/'uncommitted'; quarantine.mkdir(exist_ok=True)
                    path.rename(quarantine/(path.name+'.'+str(time.time_ns())))
            restored = read_epoch(root, max(epochs), h)
            model.load_state_dict(restored['model']); optimizer.load_state_dict(restored['optimizer'])
            torch.set_rng_state(restored['rng']); generator.set_state(restored['shuffle'])
            if args.device=='cuda': torch.cuda.set_rng_state_all(restored['cuda_rng'])
            metrics, best, best_epoch, start = restored['metrics'], restored['best'], restored['best_epoch'], restored['epoch']+1
            publish(root, restored)
        else:
            save_epoch(root, state(0))
        for epoch in range(start, args.epochs+1):
            if (root/'STOP').exists():
                shared.write_json(root/'status.json', dict(status='stopped', completed=epoch-1, queued=args.epochs-epoch+1, failed=0))
                return 2
            model.train()
            order = fit_rows[torch.randperm(len(fit_rows), generator=generator).numpy()]
            loss_sum, count = 0., 0
            last_progress = time.monotonic()
            for begin in range(0, len(order), args.batch_size):
                ix = order[begin:begin+args.batch_size]
                valid = torch.as_tensor(mask[ix], device=args.device)
                if not valid.any(): continue
                prediction = model(**tensors(obs, ix, args.device))
                target = torch.as_tensor(y[ix], device=args.device)
                loss = ((prediction[valid]-target[valid])/10).square().mean()
                if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
                optimizer.zero_grad(set_to_none=True); loss.backward()
                if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                    raise ValueError('Nonfinite training gradient')
                optimizer.step()
                n = int(valid.sum()); loss_sum += float(loss.detach())*n; count += n
                if time.monotonic()-last_progress >= 15:
                    shared.write_json(root/'status.json', dict(status='training', active_epoch=epoch,
                        completed=epoch-1, queued=args.epochs-epoch, fit_snapshots=min(begin+args.batch_size, len(order)),
                        total_fit_snapshots=len(order), failed=0))
                    last_progress = time.monotonic()
            result = evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size)
            metrics.append(dict(epoch=epoch, fit_scaled_mse=loss_sum/count, development=result))
            if result['mse'] < best: best, best_epoch = result['mse'], epoch
            save_epoch(root, state(epoch))
            status = dict(status='training', completed=epoch, queued=args.epochs-epoch, failed=0, best_epoch=best_epoch,
                          elapsed_seconds=time.monotonic()-started)
            shared.write_json(root/'status.json', status); print(json.dumps(status), flush=True)
        shared.write_json(root/'status.json', dict(status='complete', completed=args.epochs, queued=0, failed=0,
                                                 best_epoch=best_epoch, validation_access=False))
    except BaseException as exc:
        shared.write_json(root/'status.json', dict(status='failed', error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
