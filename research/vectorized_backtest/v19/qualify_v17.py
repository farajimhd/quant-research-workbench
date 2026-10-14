"""Strict speed gate: identical accounting plus actual full-generation wall."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,statistics,importlib,time
from pathlib import Path
read=lambda p:json.loads(Path(p).read_bytes())


def validated_baseline(previous,training,validation,predecessor,baseline_code):
    """Reuse only the already measured, unchanged V17 side of a prior attempt."""
    from hashlib import sha256
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    q=read(previous/'qualification.json');h=sha256();root=baseline_code/'research/vectorized_backtest'
    for version in ('v6','v7','v18'):
        for path in sorted((root/version).rglob('*.py')):
            h.update(str(path.relative_to(root)).replace('\\','/').encode());h.update(path.read_bytes().replace(b'\r\n',b'\n'))
    if q['baseline_source_sha256']!=h.hexdigest() or not q['full_generation_with_validation_qualified']:raise ValueError('Reusable V17 source or full-generation proof changed')
    for key,path in (('training_sha256',training),('validation_sha256',validation),('predecessor_checkpoint_sha256',predecessor/'checkpoint.json')):
        if q[key]!=file_hash(path):raise ValueError('Reusable V17 dataset/checkpoint changed')
    profile=read(previous/'v18-accounting/profile.json')
    if profile['source_sha256']!=h.hexdigest() or profile['result_reuse'] is not False:raise ValueError('Reusable accounting contract changed')
    checkpoint=read(previous/'v18-generation/checkpoint.json')
    if checkpoint['state']['completed']!=4 or checkpoint['contract']['population']!=1024 or checkpoint['contract']['batch_size']!=512 or checkpoint['contract']['session_workers']!=2:raise ValueError('Incomplete production-size V17 benchmark')
    if checkpoint['contract']['seed']!=182237 or checkpoint['contract']['generations']!=4 or checkpoint['contract']['backend']!='compile' or checkpoint['contract']['device']!='cuda':raise ValueError('Reusable V17 execution contract changed')
    times=[]
    for g in range(1,5):
        for role in ('training','validation'):
            if not (previous/'v18-generation'/role/f'generation-{g:04d}'/'complete.json').exists():raise ValueError('Missing reusable generation seal')
        wall=read(previous/'v18-generation/training'/f'generation-{g:04d}'/'wall.json')
        if not wall['includes_selection_validation_publication']:raise ValueError('Incomplete reusable wall timing')
        times.append(wall['full_generation_seconds'])
    if times!=q['full_generation_seconds']['v18'] or profile['wall_seconds']!=q['matched_accounting_seconds']['v18']:raise ValueError('Reusable timings changed')
    return profile,times

def worker(a):
    sys.path.insert(0,str(a.code));os.chdir(a.code)
    import torch
    torch.set_num_threads(1)
    search=importlib.import_module('research.vectorized_backtest.'+a.engine+'.run_search')
    if a.mode=='generation':
        return search.run(a.training,a.validation,a.output,population_size=1024,generations=4,batch_size=512,session_workers=2,seed=182237,capture_gib=4.,maximum_gate_gib=8.)
    import torch
    torch.set_num_threads(1)
    genome=importlib.import_module('research.vectorized_backtest.'+a.engine+'.genome')
    lanes=importlib.import_module('research.vectorized_backtest.'+a.engine+'.session_lanes')
    terminal=importlib.import_module('research.vectorized_backtest.'+a.engine+'.terminal')
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json
    data=search.bind_dataset(a.training,'training');days=[data['days'][len(data['days'])//3],data['days'][2*len(data['days'])//3]]
    population=read(a.population)['population'];members=[genome.Individual.restore(x) for x in population];token=search.digest(population)
    out=require_runtime(a.output);config=dict(inputs=data['inputs'],history=data['history'],feature_cache=data.get('feature_cache'),indicator_root=data['indicator_root'],enriched_root=data.get('enriched_root'),output=str(out),device='cuda',backend='compile',batch_size=512,execution=dict(entry_dollars=1000.,add_dollars=200.,cost_bps=0.,fee_per_share=.005,minimum_order_fee=1),maximum_data_gib=4.,maximum_gate_gib=8.,source_sha256=search.source_hash(),split='training',episode_capacity=65536,plan_limit=2,reuse_results=False,capture_gib=4.)
    contract=dict(population=1024,generations=4,batch_size=512,session_workers=2,input_receipts={d:data['receipts'][d] for d in days},execution=config['execution'],session_execution='independent');timings=[]
    # Historical accounting oracle only. Native-generation benchmarking above
    # always uses V18's protected execution and updated objectives.
    config['execution']['risk_protection']=False
    with terminal.CampaignProgress(out,contract) as panel,lanes.SessionLanes(config,2,panel) as runner:
        for repeat in range(1,5):
            panel.begin_generation(repeat);beg=time.perf_counter();runner.generation(days,members,repeat,token);timings.append(time.perf_counter()-beg)
    write_json(out/'profile.json',dict(source_sha256=search.source_hash(),days=days,population_sha256=token,wall_seconds=timings,result_reuse=False))
    return 0

def qualify(a):
    from .run_search import source_hash
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
    out=require_runtime(a.output);code=Path(__file__).resolve().parents[3]
    tests=sorted((code/'tests').glob('test_vector_v19*.py'))
    with (out/'tests.log').open('w') as log:
        subprocess.run([sys.executable,'-B','-m','pytest',*[str(t) for t in tests],'-q','-p','no:cacheprovider','--basetemp='+str(out/'tests')],stdout=log,stderr=subprocess.STDOUT,check=True)
    for role,path in (('training',a.training),('validation',a.validation)):
        d=read(path);write_json(out/(role+'-v17.json'),d)
    population=read(a.predecessor/'training/generation-0001/population.json')
    if len(population['population'])!=1024:raise ValueError('1024-member baseline required')
    write_json(out/'population.json',population);profiles={};full={}
    if a.reuse_baseline:
        profiles['v18'],full['v18']=validated_baseline(a.reuse_baseline,a.training,a.validation,a.predecessor,a.baseline_code)
        if read(a.reuse_baseline/'population.json')!=population:raise ValueError('Reusable baseline population changed')
    for mode in ('accounting','generation'):
        for engine,source in (('v18',a.baseline_code),('v19',code)):
            if engine=='v18' and a.reuse_baseline:continue
            folder=out/(engine+'-'+mode)
            if folder.exists():raise ValueError('Fresh benchmark output required')
            cmd=[sys.executable,'-B','-u',str(Path(__file__).resolve()),'--worker','--engine',engine,'--mode',mode,'--code',str(source),'--output',str(folder),'--training',str(a.training if engine=='v19' else out/'training-v17.json'),'--validation',str(a.validation if engine=='v19' else out/'validation-v17.json'),'--population',str(out/'population.json')]
            print('QUALIFY',engine,mode,flush=True)
            with (out/(engine+'-'+mode+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
            if mode=='accounting':profiles[engine]=read(folder/'profile.json')
            else:full[engine]=[read(folder/'training'/f'generation-{g:04d}'/'wall.json')['full_generation_seconds'] for g in range(1,5)]
    for g in range(1,5):
        for day in profiles['v18']['days']:
            baseline=a.reuse_baseline if a.reuse_baseline else out
            left=read(baseline/'v18-accounting'/f'generation-{g:04d}'/day/'receipt.json')['metrics'];right=read(out/'v19-accounting'/f'generation-{g:04d}'/day/'receipt.json')['metrics']
            for key,value in left.items():
                if key not in right or value!=right[key]:raise ValueError('Legacy accounting differs: '+key)
    ratio=statistics.median(full['v19'][1:])/statistics.median(full['v18'][1:]);matched=statistics.median(profiles['v19']['wall_seconds'][1:])/statistics.median(profiles['v18']['wall_seconds'][1:])
    q=dict(status='passed' if max(ratio,matched)<=1.0 else 'speed_gate_failed',source_sha256=source_hash(),baseline_source_sha256=profiles['v18']['source_sha256'],exact_accounting_parity=True,maximum_ratio=1.0,full_generation_ratio=ratio,matched_accounting_ratio=matched,full_generation_seconds=full,matched_accounting_seconds={k:v['wall_seconds'] for k,v in profiles.items()},full_generation_with_validation_qualified=True,training_sha256=file_hash(a.training),validation_sha256=file_hash(a.validation),predecessor_checkpoint_sha256=file_hash(a.predecessor/'checkpoint.json'),scope='1024/batch512/two workers; one cold and three warm generations; native searches including selection/validation/publication; separate identical legacy accounting with no reuse')
    q.update(protected_native_generation_qualified=True,legacy_accounting_scope='unprotected historical oracle only; native V18 speed run requires protection')
    if a.reuse_baseline:q.update(reused_baseline=str(a.reuse_baseline),reused_baseline_qualification_sha256=file_hash(a.reuse_baseline/'qualification.json'))
    write_json(out/'qualification.json',q);print(json.dumps(q),flush=True);return 0 if q['status']=='passed' else 2

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('training','validation','output'):p.add_argument('--'+key,type=Path,required=True)
    for key in ('baseline-code','predecessor','code','population'):p.add_argument('--'+key,type=Path)
    p.add_argument('--reuse-baseline',type=Path)
    p.add_argument('--worker',action='store_true');p.add_argument('--engine',choices=('v18','v19'));p.add_argument('--mode',choices=('accounting','generation'));a=p.parse_args()
    if a.worker:return worker(a)
    if not a.baseline_code or not a.predecessor:raise ValueError('Pinned predecessor required')
    return qualify(a)
if __name__=='__main__':raise SystemExit(main() or 0)
