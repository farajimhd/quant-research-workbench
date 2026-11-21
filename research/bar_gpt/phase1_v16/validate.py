"""Profile or fully validate a pinned candidate on the original Phase0 split."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,threading,time
from pathlib import Path
import torch
from .model import Autoencoder
from ..phase1_v12.model import Config
from ..phase1_v12.data import load_teacher
from ..phase1_v12.train import evaluate
from ..v5.data_v3 import load_split
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json
from ..v5.reporting_v1 import Reporter
from ..phase2_v2.train import lease
from research.mlops.env import discover_env_files,load_env_files


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--variant',choices=['base','body','linear_body','slots_body'],required=True)
    p.add_argument('--profile-batches',type=int,default=0);p.add_argument('--batch-size',type=int,default=4096);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--seconds',type=int,default=3000);a=p.parse_args()
    if a.output.exists():raise ValueError('New validation identity required')
    if not 1<=a.seconds<=3000 or not 1<=a.batch_size<=8192 or not 0<=a.workers<=32 or a.profile_batches<0:raise ValueError('Invalid bounded validation settings')
    a.output.mkdir(parents=True);a.validation_batch_size=a.batch_size;a.validation_workers=a.workers;a.graphs=True;a.metric_graphs=True
    import psutil,sys
    write_json(a.output/'process.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),command=sys.argv))
    a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');calpath=root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json'
    cal=json.loads(calpath.read_text());split=root/'phase1-v6-cohort-balanced-v4/split.json';manifest=load_split(split)
    if manifest['manifest_hash']!=cal['split_hash']:raise ValueError('Split changed')
    teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text())
    if file_hash(a.checkpoint)!=receipt['checkpoint_sha']:raise ValueError('Checkpoint hash mismatch')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    c=Config(**saved['contract'].get('config',vars(Config())))
    if saved['contract']['arm']!=a.variant:raise ValueError('Checkpoint architecture mismatch')
    m=Autoencoder(c,cal['mean'],cal['scale'],a.variant).cuda()
    m.load_state_dict({k:v for k,v in saved['model'].items() if k not in ('encoder.input_center','encoder.input_scale')})
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    contract=dict(source=a.source_revision,checkpoint=str(a.checkpoint),checkpoint_sha256=receipt['checkpoint_sha'],variant=a.variant,split_hash=cal['split_hash'],calibration_sha256=file_hash(calpath),profile_batches=a.profile_batches,batch_size=a.batch_size,workers=a.workers,deadline_seconds=a.seconds)
    write_json(a.output/'manifest.json',contract)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False);os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
    reporter=Reporter(a.output,dict(model=contract,output=str(a.output)),mode='online',project='bargpt-phase1-v16-direction')
    if reporter.run is None:raise RuntimeError('Online logging required')
    def deadline():
        try:
            with (a.output/'STOP').open('x') as f:f.write(f'OWNED_DEADLINE: validation reached{a.seconds}s; partial result is not complete.')
        except FileExistsError:pass
    timer=threading.Timer(a.seconds,deadline);timer.daemon=True;timer.start()
    try:
        with lease(a.output):
            result=evaluate(m,teacher,manifest,a,reporter,0,limit_batches=a.profile_batches or None)
            if result is None:reporter.finish('stopped');return
            write_json(a.output/'result.json',dict(metrics=result[1],windows=result[0]['windows'],full_population=result[0]['full_population'],seconds=result[0]['seconds'],wandb_url=reporter.run.url))
            reporter.finish('complete')
    except BaseException as exc:reporter.finish('failed',repr(exc));raise
    finally:timer.cancel()


if __name__=='__main__':main()
