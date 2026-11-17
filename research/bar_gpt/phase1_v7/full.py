"""Full V7 training entry point: explicit immutable source and runtime ownership."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1',CUBLAS_WORKSPACE_CONFIG=':4096:8')
import argparse,json,time,random
from pathlib import Path
import numpy as np
import torch
from ..v5.shards.phase1_contract import write_json,file_hash,digest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=('qualify','engineering','diagnostics','compute','stream','validation-profile','train'))
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--deployment',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path(r'D:\TradingML\runtimes\bar_gpt\encoder-campaign2-20261117\dataset'))
    p.add_argument('--checkpoint',type=Path,default=Path(r'D:\TradingML\runtimes\bar_gpt\encoder-campaign2-20261117\finalist-audit\e08-coupled-quotes-pinned.pt'))
    p.add_argument('--split',type=Path,default=Path(r'D:\TradingML\runtimes\bar_gpt\phase1-v6-cohort-balanced-v4\split.json'))
    p.add_argument('--batch-size',type=int,default=8192);p.add_argument('--workers',type=int,default=32)
    p.add_argument('--prefetch',type=int,default=4);p.add_argument('--precision',choices=('fp32','bf16'),default='fp32')
    p.add_argument('--shard-prefetch',type=int,default=16);p.add_argument('--shard-prefetch-mib',type=int,default=64)
    p.add_argument('--validation-batch-size',type=int,default=0)
    p.add_argument('--validation-graph',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--cuda-graph',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--preparation',choices=('cpu','gpu','raw_gpu'),default='cpu')
    p.add_argument('--batches',type=int,nargs='+',default=[512,1024,2048,4096,8192])
    p.add_argument('--steps',type=int,default=40);p.add_argument('--seed',type=int,default=17)
    p.add_argument('--epochs',type=int,default=50);p.add_argument('--resume',action='store_true')
    p.add_argument('--engineering-receipt',type=Path)
    p.add_argument('--checkpoint-every',type=int,default=1000)
    p.add_argument('--max-updates',type=int,default=0,help='Explicit engineering smoke boundary; zero means full schedule')
    p.add_argument('--learning-rate',type=float,default=3e-4);p.add_argument('--min-learning-rate',type=float,default=3e-5)
    p.add_argument('--warmup-updates',type=int,default=1000)
    p.add_argument('--wandb-mode',choices=('online','disabled'),default='online')
    a=p.parse_args()
    if not 0<=a.workers<=32 or not 1<=a.prefetch<=4 or min(a.batch_size,a.steps,a.epochs)<1:raise ValueError('Invalid bounded configuration')
    if not 0<=a.shard_prefetch<=64 or not 1<=a.shard_prefetch_mib<=1024 or not 0<=a.validation_batch_size<=65536:raise ValueError('Invalid bounded prefetch/validation configuration')
    if not a.output.resolve().is_relative_to(Path(r'D:\TradingML\runtimes').resolve()):raise ValueError('Runtime authority required')
    if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise RuntimeError('Human STOP remains in force')
    proof=json.loads(a.deployment.read_text());root=Path(__file__).resolve().parents[3]
    if proof['revision']!=a.source_revision:raise ValueError('Deployment revision mismatch')
    for name,h in proof['files'].items():
        if file_hash(root/name)!=h:raise ValueError(f'Immutable source changed: {name}')
    a.output.mkdir(parents=True,exist_ok=a.resume)
    write_json(a.output/('resume-arguments.json' if a.resume else 'arguments.json'),{k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    import psutil
    own=psutil.Process();write_json(a.output/'process.json',dict(pid=own.pid,created=own.create_time(),command=own.cmdline()))
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise RuntimeError('Full-run GPU authority is the workstation')
    if psutil.virtual_memory().available<64*2**30 or psutil.disk_usage(str(a.output)).free<150*2**30:raise RuntimeError('Required RAM/disk reserve unavailable')
    import subprocess
    owners=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name','--format=csv,noheader'],text=True)
    desktop_names={'dwm.exe','shellhost.exe','crossdeviceresume.exe','startmenuexperiencehost.exe','searchhost.exe',
        'explorer.exe','shellexperiencehost.exe','msedgewebview2.exe','lockapp.exe','logioptionsplus_agent.exe',
        'textinputhost.exe','taskmgr.exe','chatgpt.exe','codex-computer-use-swift.exe','windowsterminal.exe'}
    desktop=[];other=[]
    for line in owners.splitlines():
        fields=line.split(',',1)
        if not fields[0].strip().isdigit():continue
        pid=int(fields[0]);name=fields[1].strip().lower() if len(fields)>1 else ''
        if pid==own.pid:continue
        if pid==4 or (name.startswith(('c:\\windows\\','c:\\program files\\','c:\\program files (x86)\\')) and Path(name).name in desktop_names):desktop.append(dict(pid=pid,path=name))
        else:other.append(pid)
    write_json(a.output/'gpu-ownership.json',dict(desktop_clients=desktop,other_compute_pids=other))
    if other:raise RuntimeError(f'GPU scheduling conflict: {other}')
    torch.set_num_threads(1);torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    torch.set_float32_matmul_precision('highest');torch.use_deterministic_algorithms(True)
    state=dict(state='running',started=time.time(),updated=time.time(),mode=a.mode,epochs=a.epochs)
    def status(**kwargs):
        state.update(kwargs,updated=time.time());write_json(a.output/'status.json',state)
    status(stage='verifying source and hardware')
    aux=json.loads((a.data/'manifest.json').read_text())
    if aux['hash']!=digest({k:v for k,v in aux.items() if k!='hash'}):raise ValueError('Auxiliary dataset seal changed')
    names=['train.npy']
    if a.mode in ('qualify','engineering'):names+=['development.npy','tiny.npy']
    elif a.mode in ('train','diagnostics'):names+=['development.npy']
    for name in names:
        if file_hash(a.data/name)!=aux['files'][name]:raise ValueError(f'Auxiliary data changed: {name}')
    if a.mode in ('qualify','validation-profile','diagnostics') and file_hash(a.checkpoint)!='69d46de6c1ae6950292c90ae56f9f6cbe6619a11c1c772c8f4cbea65ae979dec':
        raise ValueError('Pinned finalist checkpoint changed')
    write_json(a.output/'auxiliary-data.json',dict(hash=aux['hash'],files={n:aux['files'][n] for n in names}))
    write_json(a.output/'hardware.json',dict(host=os.environ.get('COMPUTERNAME'),gpu=torch.cuda.get_device_name(),
        memory_mib=torch.cuda.get_device_properties(0).total_memory/2**20,torch=str(torch.__version__),cuda=torch.version.cuda,
        ram_available=psutil.virtual_memory().available,disk_free=psutil.disk_usage(str(a.output)).free))
    from ..phase2_v2.train import lease
    try:
        with lease(a.output):
            if a.mode in ('qualify','compute','engineering'):
                from . import full_profile
                result=getattr(full_profile,a.mode)(a)
            elif a.mode=='diagnostics':
                from .full_profile import model
                from .full_diagnostics import run
                m=model();m.load_state_dict(torch.load(a.checkpoint,map_location='cpu',weights_only=False)['model'])
                result=run(m,a,12000)
            else:
                from ..v5.data_v3 import load_split
                from ..v5.loader_index_v1 import attach
                status(stage='verifying full split and compact loader index')
                manifest=load_split(a.split)
                manifest=attach(manifest,Path(manifest['dataset']))
                manifest['splits']={}
                status(epoch_total=manifest['counts']['train'],validation_total=manifest['counts']['validation'])
                if a.mode=='stream':
                    from .full_profile import stream
                    result=stream(a,manifest,status)
                elif a.mode=='validation-profile':
                    from .full_profile import model
                    from .full_train import evaluate
                    m=model();m.load_state_dict(torch.load(a.checkpoint,map_location='cpu',weights_only=False)['model'])
                    result=evaluate(m,manifest,a,status,limit=a.steps)
                    write_json(a.output/'validation-profile.json',result)
                else:
                    from .full_train import train
                    result=train(a,manifest,status)
            status(state='complete' if a.mode!='train' else result,stage=str(result) if isinstance(result,str) else 'profile complete')
            concise={k:v for k,v in result.items() if '/' not in k and isinstance(v,(int,float,bool,str))} if isinstance(result,dict) else result
            print(json.dumps(concise),flush=True)
    except BaseException as e:
        status(state='failed',stage='failed',error=f'{type(e).__name__}: {e}');raise


if __name__=='__main__':main()
