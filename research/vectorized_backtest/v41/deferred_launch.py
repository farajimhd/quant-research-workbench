"""Exclusive laptop queue; waiting does not import Torch or initialize CUDA."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for _key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[_key]='1'
for _key,_value in dict(V41_CPU_RESERVE_GIB='12',V41_CPU_ADMISSION_GIB='8',V41_PREPARATION_WORKERS='2',V41_NEURAL_BLOCK='128').items():os.environ[_key]=_value
import argparse,json,hashlib,shutil,subprocess,sys,time,traceback
from pathlib import Path
import psutil


def read(path):return json.loads(Path(path).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,sort_keys=True,indent=2));tmp.replace(path)
def live(owner):
    try:return abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
    except psutil.NoSuchProcess:return False


def predecessor(job):
    """Require the pinned 100-generation completion, exact owners and all seals."""
    old=job['predecessor'];campaign=Path(old['campaign']);root=Path(old['root'])
    if read(root/'active-campaign.json')!=old['owner']:raise ValueError('V40 pointer changed')
    identities=Path(job['root'])/'diagnostics/predecessor-descendants.json'
    seen=read(identities) if identities.exists() else []
    if live(old['owner']):
        try:seen.extend(dict(pid=p.pid,creation_time=p.create_time()) for p in psutil.Process(old['owner']['pid']).children(recursive=True))
        except psutil.NoSuchProcess:pass
        write(identities,list({(r['pid'],r['creation_time']):r for r in seen}.values()))
        return None
    if any(live(r) for r in seen) or list(campaign.rglob('owner.lock')):return None
    failure=root/'diagnostics/extend-100/failure.json'
    if failure.exists():raise RuntimeError('V40 continuation failed; inspect preserved evidence')
    if not (campaign/'exit.json').exists():raise RuntimeError('V40 owner exited without completion evidence')
    if read(campaign/'exit.json')['exit_code']!=0:raise RuntimeError('V40 did not complete successfully')
    local=campaign/'local/2026-08-04';complete=read(local/'complete.json');checkpoint=read(local/'checkpoint.json')
    if complete.get('generations')!=100 or complete.get('validation_opened') is not False:raise ValueError('V40 target not sealed')
    if sha(local/'checkpoint.json')!=complete['checkpoint_sha256'] or sha(local/'archive.json')!=complete['archive_sha256']:raise ValueError('V40 completion changed')
    if checkpoint['contract']['source_sha256']!=old['owner']['source_sha256'] or checkpoint['state']['completed']!=100 or digest(checkpoint['state'])!=checkpoint['state_sha256']:raise ValueError('V40 checkpoint changed')
    for g in range(1,101):
        folder=local/f'generation-{g:04d}';seal=read(folder/'complete.json')
        if sha(folder/'complete.json')!=checkpoint['state']['seals'][str(g)]:raise ValueError('V40 seal changed')
        for key,rel in [('receipt','2026-08-04/receipt.json'),('scores','scores.json'),('births','births.json')]:
            if sha(folder/rel)!=seal[key+'_sha256']:raise ValueError('V40 generation evidence changed')
    return dict(completed=100,checkpoint_sha256=sha(local/'checkpoint.json'),state_sha256=checkpoint['state_sha256'],all_seals_verified=True,ownership_released=True)


def check(job):
    from .run_search import source_hash
    if source_hash()!=job['source_sha256']:raise ValueError('V41 source changed')
    for key in ('training','initial_pool','baseline_initial_pool','tests_result'):
        if sha(job[key])!=job[key+'_sha256']:raise ValueError(key+' changed')
    tests=read(job['tests_result'])
    if tests['exit_code']!=0 or tests['source_sha256']!=job['source_sha256'] or sha(tests['output'])!=tests['output_sha256']:raise ValueError('CPU test evidence invalid')
    previous=read(job['baseline_initial_pool']);pool=read(job['initial_pool'])
    if len(pool['population'])!=6144 or digest([dict(p,version='v34-recurrent-v1') for p in pool['population']])!=previous['population_sha256']:raise ValueError('V34 initial genomes differ')
    if pool['rng_before']!=previous['rng_before'] or pool['rng_after']!=previous['rng_after']:raise ValueError('V34 initial RNG differs')
    if pool['source_sha256']!=job['source_sha256'] or pool['financial_results_imported'] is not False:raise ValueError('Pool source/reuse changed')
    from dataclasses import asdict
    from .evaluator import Execution
    from .lineage import Selection
    from .objectives import Objective
    baseline=read(Path(job['baseline'])/'checkpoint.json')['contract']
    for key,value in dict(execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),selection=asdict(Selection()),objective=asdict(Objective()),population=1024,seed=2020,generations=50,initial_multiplier=6).items():
        if baseline[key]!=value:raise ValueError('Comparison contract changed: '+key)
    if Path(job['campaign']).exists():raise ValueError('Fresh V41 campaign already exists')
    for pointer in Path(job['root']).parent.glob('torch_backtest_v*/active-campaign.json'):
        owner=read(pointer)
        if 'pid' in owner and 'creation_time' in owner and live(owner):raise RuntimeError('Competing optimizer: '+str(pointer))
    if psutil.virtual_memory().available<20*2**30:raise MemoryError('12 GiB reserve plus 8 GiB admission required')


def run(job):
    root=Path(job['root']);proof=None
    while proof is None:
        if (root/'STOP').exists():raise InterruptedError('Queue cancelled')
        proof=predecessor(job)
        if proof is None:
            write(root/'queue-status.json',dict(stage='Waiting for V40 sealed generation100 and ownership release',status='waiting',epoch=time.time()))
            time.sleep(10)
    write(root/'diagnostics/predecessor-completion-proof.json',proof);check(job)
    def checked(name,args):
        with (root/'diagnostics'/name).open('w',encoding='utf-8') as log:
            result=subprocess.run([sys.executable.replace('pythonw.exe','python.exe'),'-B',*args],cwd=job['code'],stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=600)
        if result.returncode:raise RuntimeError(name+' failed; evidence retained')
    write(root/'queue-status.json',dict(status='replaying',stage='Frozen V40 generation100 top20 on Aug5 before V41',epoch=time.time()))
    checked('v40-g100-aug05.log',['-m','research.vectorized_backtest.v41.predecessor_replay','--job',str(root/'job.json')])
    replay=root/'diagnostics/v40-g100-aug05/complete.json'
    proof=read(replay)
    if proof['generation']!=100 or proof['candidates']!=20 or not proof['full_session'] or not proof['no_selection_feedback']:raise ValueError('V40 Aug5 replay not complete')
    write(root/'queue-status.json',dict(status='profiling',stage='Short GPU parity checks after V40 replay release',epoch=time.time()))
    checked('gpu-boundaries.log',['-m','pytest',job['cuda_test'],'-k','cuda','-q','-p','no:cacheprovider','-c',job['pytest_config'],'--basetemp',str(root/'diagnostics/pytest-cuda')])
    checked('gpu-profile.log',['-m','research.vectorized_backtest.v41.bounded_profile','--training',job['training'],'--pool',job['initial_pool'],'--output',str(root/'diagnostics/gpu-profile')])
    profile=read(root/'diagnostics/gpu-profile/complete.json')
    if profile['source_sha256']!=job['source_sha256'] or not all(profile.get(k) for k in ('exact_v34_captured_financial_parity','exact_batch_parity','exact_cold_warm_parity')):raise ValueError('GPU parity failed')
    if len(profile.get('newborn_fixture_cases',[]))!=8:raise ValueError('New crossover fixtures missing')
    if min(r['available_host_gib'] for r in profile['cases'])<12:raise MemoryError('Profile crossed host reserve')
    check(job)
    if predecessor(job) is None:raise RuntimeError('V40 ownership reappeared')
    write(root/'diagnostics/qualification.json',dict(source_sha256=job['source_sha256'],tests_sha256=job['tests_result_sha256'],profile_sha256=sha(root/'diagnostics/gpu-profile/complete.json'),v40_g100_aug05_proof_sha256=sha(replay),exact_v34_initial_payloads=True,exact_initial_rng=True,no_financial_result_imports=True,bounded=True))
    origin=root/'initialization-probes/2026-08-04.npz';destination=Path(job['campaign'])/'initialization-probes/2026-08-04.npz'
    destination.parent.mkdir(parents=True);shutil.copy2(origin,destination)
    if sha(origin)!=sha(destination):raise ValueError('Probe copy changed')
    before=root/'diagnostics/gpu-profile/compiler-cache';after=Path('D:/TradingML/runtimes/v41-cache')/str(os.getpid())/'cache/lane-0';seeded=[]
    for group in ('inductor','triton','extensions'):
        for path in (before/group).rglob('*'):
            if not path.is_file() or path.suffix in ('.lock','.tmp') or 'locks' in path.parts:continue
            target=after/path.relative_to(before);target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copy2(path,target)
            if sha(target)!=sha(path):raise ValueError('Compiler copy changed')
            seeded.append(dict(path=str(path.relative_to(before)),sha256=sha(path)))
    write(root/'diagnostics/launch-compiler-seed.json',dict(source_sha256=job['source_sha256'],files=seeded))
    old_observer=Path(job['predecessor']['root'])/'observer.json'
    if old_observer.exists():
        owner=read(old_observer)
        if live(owner):
            p=psutil.Process(owner['pid'])
            if p.cmdline()[-1]!='research.vectorized_backtest.v40.local_details':raise ValueError('Unexpected observer identity')
            p.terminate()
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
    panel=subprocess.Popen([sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.vectorized_backtest.v41.local_details'],cwd=job['code'],creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup)
    write(root/'observer.json',dict(pid=panel.pid,creation_time=psutil.Process(panel.pid).create_time()))
    os.environ['V41_INITIAL_POOL']=job['initial_pool']
    write(root/'queue-status.json',dict(status='launching',epoch=time.time()))
    from .run_train import main as train
    code=train(['--training',job['training'],'--output',job['campaign'],'--mode','local','--day','2026-08-04','--authorize-run','--commit',job['commit'],'--population','1024','--initial-multiplier','6','--generations','50','--batch-size','1024','--seed','2020','--host-reserve-gib','12','--host-admission-gib','8'])
    write(root/'launcher-exit.json',dict(exit_code=code,epoch=time.time()))
    if code==0:
        from .compare_v32 import main as compare
        sys.argv=['compare','--output',job['campaign'],'--baseline',job['baseline']];compare()
        sys.argv=['compare','--output',job['campaign'],'--baseline',job['v40_baseline']];compare()
    return code


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();job=read(a.job);root=Path(job['root']);lock=root/'queue-owner.json'
    with lock.open('x') as stream:json.dump(dict(pid=os.getpid(),creation_time=psutil.Process().create_time()),stream)
    sys.stdout=(root/'queue-output.log').open('a',encoding='utf-8',buffering=1);sys.stderr=sys.stdout
    try:return run(job)
    except BaseException as error:
        write(root/'queue-failure.json',dict(error=repr(error),epoch=time.time()));traceback.print_exc();return 1
    finally:lock.unlink();sys.stdout.close()


if __name__=='__main__':sys.exit(main())
