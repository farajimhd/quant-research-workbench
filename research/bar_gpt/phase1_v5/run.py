"""V5 isolated profiling or explicitly authorized fresh/resumed training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
if __package__ in (None,''):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]));__package__='research.bar_gpt.phase1_v5'
import argparse,json,time,sys,math
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from ..v5.shards.contract import digest,file_hash,write_json
from ..v5.data_v3 import load_split,validate_complete_market
from ..v5.loader_index_v1 import attach,Entries
from ..phase2_v2.train import lease
from .model import Config,Autoencoder
from .objectives import Stats,fit_stats
CALIBRATION_VERSION='v5-training-six-frame-stratified-loss-scales-v3'


def arguments(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('profile','train'),default='profile')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--split-manifest',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--calibration',type=Path)
    p.add_argument('--source-revision',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--precision',choices=('fp32','bf16'),default='bf16')
    p.add_argument('--feature-drop',type=float,default=.075)
    p.add_argument('--encoder-width',type=int,choices=(128,256,512),default=512,
                   help='Approved profiling alternatives; all export512; capacity differs')
    p.add_argument('--embedding-width',type=int,choices=(256,512),default=512,
                   help='Approved exported-bottleneck alternatives; decoder follows this width')
    p.add_argument('--batch-size',type=int,default=1024)
    p.add_argument('--batches',default='256,1024,4096')
    p.add_argument('--workers',type=int,default=8)
    p.add_argument('--prefetch',type=int,default=2)
    p.add_argument('--shard-prefetch',type=int,default=16)
    p.add_argument('--shard-prefetch-mib',type=int,default=64)
    p.add_argument('--threads',type=int,default=1)
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--steps',type=int,default=24)
    p.add_argument('--warmup',type=int,default=4)
    p.add_argument('--epochs',type=int,default=50)
    p.add_argument('--learning-rate',type=float,default=3e-4)
    p.add_argument('--min-learning-rate',type=float,default=5e-5)
    p.add_argument('--warmup-updates',type=int,default=1000)
    p.add_argument('--log-every',type=int,default=10)
    p.add_argument('--checkpoint-every',type=int,default=1000,help='Overwrite rolling recovery checkpoint; no per-update archives')
    p.add_argument('--train-steps',type=int,default=0,help='0 means full training pass; positive is an explicit bounded smoke')
    p.add_argument('--validation-steps',type=int,default=0,help='0 means full validation; positive is explicitly partial')
    p.add_argument('--cuda-graph',action='store_true')
    p.add_argument('--deterministic',action=argparse.BooleanOptionalAction,default=True,
                   help='Reproducible embedding reductions; required for accepted graph replay')
    p.add_argument('--resume',type=Path)
    p.add_argument('--wandb-mode',choices=('online','disabled'),default='online')
    a=p.parse_args(argv)
    if a.mode=='train' and not a.execute:p.error('Training requires --execute')
    if min(a.batch_size,a.steps,a.epochs,a.threads,a.log_every,a.checkpoint_every,a.shard_prefetch_mib)<1 or a.warmup<0:p.error('Invalid bounded resources')
    if not 0<=a.workers<=32 or not 1<=a.prefetch<=4 or not 0<=a.shard_prefetch<=64:p.error('Invalid worker/prefetch bound')
    if not 0<=a.feature_drop<=.1 or not 0<a.min_learning_rate<=a.learning_rate:p.error('Invalid feature dropout/schedule')
    if not all(math.isfinite(x) for x in (a.learning_rate,a.min_learning_rate)):p.error('Nonfinite schedule')
    try:
        if any(not 1<=int(x)<=8192 for x in a.batches.split(',')):raise ValueError
    except ValueError:p.error('Profile batches must be comma-separated integers in1..8192')
    if a.train_steps<0 or a.validation_steps<0 or a.warmup_updates<0:p.error('Negative limits are invalid')
    if a.cuda_graph and (a.device!='cuda' or a.precision!='bf16'):p.error('Capture requires CUDA/BF16')
    if a.cuda_graph and not a.deterministic:p.error('V5 capture requires deterministic qualification')
    if a.mode=='profile' and a.resume:p.error('Profiles never resume or overwrite training state')
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):p.error('Use the required runtime root')
    return a


def calibration(manifest):
    from .sampling import sample_records
    raw,proof=sample_records(manifest,'train',256)
    def batches():
        offset=0
        for evidence in proof:
            rows=raw[offset:offset+len(evidence['indices'])];offset+=len(rows)
            yield (torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
                torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()),evidence)
    return fit_stats(batches(),manifest['manifest_hash']),raw


def main(argv=None):
    a=arguments(argv);print('Command: '+__import__('subprocess').list2cmdline([sys.executable,'-B',__file__,*(sys.argv[1:] if argv is None else argv)]),flush=True)
    if (a.output/'STOP').exists():raise ValueError('STOP remains in force; no implicit restart')
    if a.output.exists() and not a.resume:raise FileExistsError('Use a new output directory')
    if a.device=='cuda':
        from ..phase2_v2.benchmark import active_trainers
        if active_trainers():raise RuntimeError('Another BarGPT trainer owns the GPU; no contention allowed')
        import psutil,shutil
        if psutil.virtual_memory().available<64*2**30 or shutil.disk_usage(a.output.parent).free<150*2**30:
            raise MemoryError('Preserve64GiB RAM and150GiB disk reserves')
    torch.set_num_threads(a.threads);torch.manual_seed(a.seed)
    torch.use_deterministic_algorithms(a.deterministic)
    a.output.mkdir(parents=True,exist_ok=bool(a.resume));last={}
    def status(**values):
        last.update(values,updated=time.time());write_json(a.output/'progress.json',last)
    try:
        import psutil
        own=psutil.Process();write_json(a.output/'process.json',dict(pid=own.pid,created=own.create_time(),command=own.cmdline()))
        status(state='profiling' if a.mode=='profile' else 'starting',stage='verifying frozen balanced split',step=0,
            epochs=a.epochs,epoch=0,mode=a.mode)
        with lease(a.output):
            manifest=load_split(a.split_manifest)
            # Existing V4 index is hash-bound to this same split. No shard changes.
            index_root=Path(manifest['dataset'])
            existing=index_root/('loader-index-v1-'+manifest['manifest_hash'][:20])/'index.json'
            manifest=attach(manifest,index_root if existing.is_file() else a.output)
            a.output.joinpath('loader-index-reference.json').write_text(json.dumps(manifest['loader_index']))
            manifest['splits']={} # workers only receive authenticated compact indices
            if a.resume and a.calibration is None:a.calibration=a.output/'calibration.json'
            if a.calibration:
                c=json.loads(a.calibration.read_text())
                if c['hash']!=digest({k:v for k,v in c.items() if k!='hash'}):raise ValueError('V5 calibration seal changed')
                if c['version']!=CALIBRATION_VERSION:raise ValueError('V5 requires explicit six-frame calibrated scales; no implicit migration')
                stats=Stats.load(c['stats']);raw=None
                if stats.split_hash!=manifest['manifest_hash']:raise ValueError('Loss calibration split changed')
            else:stats,raw=calibration(manifest)
            c=dict(version=CALIBRATION_VERSION,stats=stats.state());c['hash']=digest(c)
            write_json(a.output/'calibration.json',c)
            config=Config(width=a.encoder_width,embedding_width=a.embedding_width,
                          feedforward=4*a.encoder_width,feature_drop=a.feature_drop)
            files=list(Path(__file__).parent.glob('*.py'))
            parent=Path(__file__).parent.parent
            files += [parent/'v5'/name for name in ('data_v3.py','shard_prefetch_v3.py','loader_index_v1.py',
                      'pretraining_v3.py','encoder_v3.py','transforms_v1.py','price_relative_v3.py')]
            files += [parent/'phase2_v2/tokenizer.py']
            implementation=digest({str(p.relative_to(parent)):file_hash(p) for p in files})
            contract=dict(design=config.contract(),split=manifest['manifest_hash'],calibration=c['hash'],implementation=implementation,
                batch_size=a.batch_size,workers=a.workers,prefetch=a.prefetch,shard_prefetch=a.shard_prefetch,
                shard_prefetch_mib=a.shard_prefetch_mib,precision=a.precision,seed=a.seed,epochs=a.epochs,
                peak=a.learning_rate,floor=a.min_learning_rate,warmup=a.warmup_updates,train_steps=a.train_steps,
                validation_steps=a.validation_steps,cuda_graph=a.cuda_graph,source_revision=a.source_revision,
                checkpoint_every=a.checkpoint_every,torch_version=str(torch.__version__),cuda=torch.version.cuda,
                deterministic=a.deterministic,cublas_workspace=os.environ.get('CUBLAS_WORKSPACE_CONFIG'))
            status(epoch_total=manifest['counts']['train'],validation_total=manifest['counts']['validation'],
                encoder_width=config.width,embedding_width=config.embedding_width,
                gpu_total_mib=torch.cuda.get_device_properties(0).total_memory/2**20 if a.device=='cuda' else None)
            if not a.resume:write_json(a.output/'manifest.json',dict(contract=contract,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}))
            if a.mode=='profile':
                from .profiling import run
                result=run(manifest,stats,config,a,status,raw);write_json(a.output/'profile.json',result)
                status(state='profile_complete',stage='profile complete; full training not started',profile=result)
            else:
                from .engine import train
                model=Autoencoder(config).to(a.device);state=train(model,manifest,stats,contract,a,status)
                status(state='bounded_complete' if state=='complete' and a.train_steps else state,
                    stage='bounded smoke complete; full passes not claimed' if state=='complete' and a.train_steps else state)
            return last
    except BaseException as e:
        status(state='failed',stage='failed',error=f'{type(e).__name__}: {e}');raise


if __name__=='__main__':main()
