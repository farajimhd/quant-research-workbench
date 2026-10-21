"""Fail-closed laptop handoff. Waiting imports neither Torch nor CUDA modules."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for _name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[_name]='1'
os.environ['V39_CPU_RESERVE_GIB']='12'
os.environ['V39_CPU_ADMISSION_GIB']='8'
os.environ['V39_PREPARATION_WORKERS']='2'
os.environ['V39_NEURAL_BLOCK']='128'
import argparse,hashlib,json,shutil,subprocess,sys,time,traceback
from pathlib import Path
import psutil


def read(path):return json.loads(Path(path).read_bytes())
def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,sort_keys=True,indent=2));temporary.replace(path)


def live(identity):
    try:return abs(psutil.Process(identity['pid']).create_time()-identity['creation_time'])<.01
    except psutil.NoSuchProcess:return False


def inspect_predecessor(job):
    old=job['predecessor'];root=Path(old['root']);campaign=Path(old['campaign'])
    if digest(read(root/'job.json'))!=old['job_sha256']:raise ValueError('Predecessor job changed')
    for path in (campaign/'failure.json',root/'launcher-failure.json'):
        if path.exists():raise RuntimeError('Predecessor failed; withholding this run')
    pointer_path=root/'active-campaign.json'
    if not pointer_path.exists():return None
    pointer=read(pointer_path)
    for key in ('source_sha256','campaign','commit'):
        if pointer[key]!=old[key]:raise ValueError('Predecessor pointer changed: '+key)
    observed=Path(job['root'])/'predecessor-owner.json'
    if observed.exists():
        if read(observed)!=pointer:raise ValueError('Predecessor exact owner changed')
    else:
        if 'pid' in old and (old['pid']!=pointer['pid'] or abs(old['creation_time']-pointer['creation_time'])>=.01):raise ValueError('Unexpected predecessor owner')
        write(observed,pointer)
    process_file=Path(job['root'])/'predecessor-processes.json'
    identities=read(process_file) if process_file.exists() else []
    if live(pointer):
        identities.extend(dict(pid=p.pid,creation_time=p.create_time()) for p in psutil.Process(pointer['pid']).children(recursive=True))
        identities=list({(p['pid'],p['creation_time']):(p) for p in identities}.values());write(process_file,identities)
        return None
    if any(live(identity) for identity in identities) or list(campaign.rglob('owner.lock')):return None
    if not (root/'launcher-exit.json').exists():return None
    if read(root/'launcher-exit.json')['exit_code']!=0 or read(campaign/'exit.json')['exit_code']!=0:raise ValueError('Predecessor exit not successful')
    local=campaign/'local/2026-08-04';seal=read(local/'complete.json');checkpoint=read(local/'checkpoint.json');generations=old['generations']
    if seal.get('status')!='complete' or seal.get('generations')!=generations or seal.get('validation_opened') is not False:raise ValueError('Predecessor not complete')
    if file_hash(local/'checkpoint.json')!=seal['checkpoint_sha256'] or file_hash(local/'archive.json')!=seal['archive_sha256']:raise ValueError('Predecessor final artifact mismatch')
    if checkpoint['contract']['source_sha256']!=old['source_sha256'] or checkpoint['state']['completed']!=generations or digest(checkpoint['state'])!=checkpoint['state_sha256']:raise ValueError('Predecessor checkpoint mismatch')
    for generation in range(1,generations+1):
        folder=local/f'generation-{generation:04d}';complete=read(folder/'complete.json')
        if file_hash(folder/'complete.json')!=checkpoint['state']['seals'][str(generation)]:raise ValueError('Generation seal changed')
        for field,path in [('receipt',folder/'2026-08-04/receipt.json'),('scores',folder/'scores.json'),('births',folder/'births.json')]:
            if file_hash(path)!=complete[field+'_sha256']:raise ValueError('Generation artifact changed: '+field)
        receipt=read(folder/'2026-08-04/receipt.json')
        if receipt['population_sha256']!=complete['population_sha256'] or receipt['validation_opened'] is not False or not receipt['full_session']:raise ValueError('Incomplete financial receipt')
    return dict(checkpoint_sha256=file_hash(local/'checkpoint.json'),state_sha256=checkpoint['state_sha256'],completed=generations,all_generation_hashes_verified=True,ownership_released=True,epoch=time.time())



def verify_initial_parity(payload,before,after,previous,version):
    # Genome payloads contain tuples; JSON checkpoints restore lists. Compare
    # the canonical serialized value, not Python container implementation.
    normalized=[dict(p,version=version+'-recurrent-v1') for p in previous['population']]
    if digest(normalized)!=digest(payload) or before!=previous['rng_before'] or after!=previous['rng_after']:
        raise ValueError('Fresh initial sampling changed unexpectedly')


def prepare_pool(job):
    # Called only after inspect_predecessor returns verified completion.
    import numpy as np
    from .probes import local_probe
    from .calibration import load_probe
    from .initial_population import initialize
    path=local_probe(Path(job['training']),'2026-08-04',Path(job['root'])/'initialization-probes')
    probe=load_probe(path,job['training']);rng=np.random.default_rng(np.random.SeedSequence([2020,20260804]))
    before=rng.bit_generator.state;members,report=initialize(rng,6144,probe=probe);payload=[m.payload() for m in members]
    saved=dict(source_sha256=job['source_sha256'],probe_sha256=probe['sha256'],rng_before=before,rng_after=rng.bit_generator.state,
        population=payload,population_sha256=digest(payload),report=report)
    write(job['initial_pool'],saved)
    # Verify fresh deterministic initialization against the sealed V36 initial
    # pool. This is candidate/RNG parity, never reuse of financial evaluations.
    if file_hash(job['baseline_initial_pool'])!=job['baseline_initial_pool_sha256']:raise ValueError('V36 initial pool changed')
    previous=read(job['baseline_initial_pool'])
    verify_initial_parity(payload,before,rng.bit_generator.state,previous,job['version'])
    write(Path(job['root'])/'diagnostics/initial-parity-v36.json',dict(exact_payload_parity=True,exact_rng_parity=True,financial_results_reused=False,population_sha256=digest(payload)))


def check_job(job):
    from .run_search import source_hash
    if source_hash()!=job['source_sha256']:raise ValueError('Frozen V39 source changed')
    for key in ('training','tests_result'):
        if file_hash(job[key])!=job[key+'_sha256']:raise ValueError(key+' changed')
    tests=read(job['tests_result'])
    if tests['exit_code']!=0 or tests['source_sha256']!=job['source_sha256']:raise ValueError('CPU tests not source-bound')
    if file_hash(tests['output'])!=tests['output_sha256']:raise ValueError('CPU test output changed')
    from dataclasses import asdict
    from .evaluator import Execution
    from .lineage import Selection
    from .composition import settings
    from .objectives import Objective
    baseline=read(Path(job['baseline'])/'checkpoint.json')['contract']
    execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1))
    if execution!=job['execution']:raise ValueError('Execution ablation changed')
    expected=dict(baseline['execution'])
    expected.update(maximum_position_risk=job['execution']['maximum_position_risk'],maximum_stop_distance=job['execution']['maximum_stop_distance'])
    if execution!=expected:raise ValueError('Unrelated execution setting changed')
    recombination=settings(Selection());new=dict(recombination.pop('crossover_v39'));new.pop('entry_residual');old=baseline['recombination']['crossover_v36']
    if new!=old:raise ValueError('Unrelated crossover setting changed')
    recombination['crossover_v36']=old;recombination['version']=baseline['recombination']['version']
    for key,value in dict(selection=asdict(Selection()),objective=asdict(Objective()),recombination=recombination,
        population=1024,seed=2020,generations=50,initial_multiplier=6,day='2026-08-04').items():
        if baseline[key]!=value:raise ValueError('V36 comparison contract changed: '+key)
    if Path(job['campaign']).exists():raise ValueError('V39 campaign exists; no duplicate fresh launch')
    if psutil.virtual_memory().available<20*2**30:raise MemoryError('12 GiB reserve plus 8 GiB load admission required')


def run(job):
    root=Path(job['root']);proof=None
    while proof is None:
        if (root/'STOP').exists():raise InterruptedError('Deferred launch cancelled')
        proof=inspect_predecessor(job)
        if proof is None:
            write(root/'deferred-status.json',dict(status='waiting',stage='Predecessor completion and ownership release',epoch=time.time(),pid=os.getpid(),creation_time=psutil.Process().create_time()))
            time.sleep(20)
    write(root/'diagnostics/predecessor-completion-proof.json',proof)
    check_job(job)
    prepare_pool(job)
    # Independent children finish before the optimizer can acquire CUDA.
    def checked(name,args):
        with (root/'diagnostics'/name).open('w',encoding='utf-8') as log:
            result=subprocess.run([sys.executable.replace('pythonw.exe','python.exe'),'-B',*args],stdout=log,stderr=subprocess.STDOUT,
                cwd=job['code'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=600)
        if result.returncode:raise RuntimeError(name+' failed; see preserved output')
    checked('gpu-boundaries.log',['-m','pytest',job['cuda_test'],'-k','cuda','-q','-p','no:cacheprovider','-c',job['pytest_config'],'--basetemp',str(root/'diagnostics/pytest-cuda')])
    checked('gpu-profile.log',['-m','research.vectorized_backtest.v39.bounded_profile','--training',job['training'],'--pool',job['initial_pool'],'--output',job['profile_root']])
    profile=read(Path(job['profile_root'])/'complete.json')
    if profile['source_sha256']!=job['source_sha256'] or not all(profile.get(k) for k in ('eager_captured_reference_passed','exact_batch_parity','exact_cold_warm_parity')):raise ValueError('GPU qualification failed')
    check_job(job)
    # Repeat old-owner release audit immediately before full launch.
    if inspect_predecessor(job) is None:raise RuntimeError('Predecessor ownership not released')
    write(root/'diagnostics/qualification.json',dict(source_sha256=job['source_sha256'],profile_sha256=file_hash(Path(job['profile_root'])/'complete.json'),cpu_tests_sha256=job['tests_result_sha256'],gpu_boundary_tests_passed=True,profile=profile,bounded=True))
    os.environ['V39_INITIAL_POOL']=job['initial_pool']
    # Reuse only this deployment's just-qualified compiler output, never replay
    # results. Runtime specialization guards remain authoritative.
    before=Path(job['profile_root'])/'compiler-cache'
    after=Path('D:/TradingML/runtimes/v39-cache')/str(os.getpid())/'cache/lane-0'
    seeded=[]
    for group in ('inductor','triton','extensions'):
        for path in (before/group).rglob('*'):
            if not path.is_file() or path.suffix in ('.lock','.tmp') or 'locks' in path.parts:continue
            target=after/path.relative_to(before);target.parent.mkdir(parents=True,exist_ok=True)
            expected=file_hash(path)
            if not target.exists():shutil.copy2(path,target)
            if file_hash(target)!=expected:raise ValueError('Qualified compiler artifact changed')
            seeded.append(dict(path=str(path.relative_to(before)),sha256=expected))
    write(root/'diagnostics/launch-compiler-seed.json',dict(source_sha256=job['source_sha256'],files=seeded))
    # Avoid rebuilding the identical probe or changing its exact NPZ identity.
    origin=root/'initialization-probes/2026-08-04.npz'
    destination=Path(job['campaign'])/'initialization-probes/2026-08-04.npz'
    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(origin,destination)
    if file_hash(origin)!=file_hash(destination):raise ValueError('Prepared probe copy changed')
    # One normal, nonmodal observer; launch from the same immutable deployment.
    observer_path=Path(job['predecessor']['root'])/'observer.json'
    observer=read(observer_path) if observer_path.exists() else None
    if observer and live(observer):psutil.Process(observer['pid']).terminate()
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
    panel=subprocess.Popen([sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.vectorized_backtest.v39.local_details'],cwd=job['code'],creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup)
    write(root/'observer.json',dict(pid=panel.pid,creation_time=psutil.Process(panel.pid).create_time()))
    write(root/'deferred-status.json',dict(status='launching',epoch=time.time(),source_sha256=job['source_sha256']))
    from .run_train import main
    code=main(['--training',job['training'],'--output',job['campaign'],'--mode','local','--day','2026-08-04','--authorize-run','--commit',job['commit'],
        '--population','1024','--initial-multiplier','6','--generations','50','--batch-size','1024','--seed','2020','--host-reserve-gib','12','--host-admission-gib','8'])
    write(root/'launcher-exit.json',dict(exit_code=code,epoch=time.time()))
    if code==0:
        from .compare_v32 import compare
        compare(job)
    return code


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();job=read(a.job);root=Path(job['root'])
    lock=root/'deferred-owner.json'
    with lock.open('x') as stream:json.dump(dict(pid=os.getpid(),creation_time=psutil.Process().create_time()),stream)
    log=(root/'train-output.log').open('a',encoding='utf-8',buffering=1);sys.stdout=log;sys.stderr=log
    try:return run(job)
    except BaseException as error:
        write(root/'launcher-failure.json',dict(error=repr(error),epoch=time.time()));traceback.print_exc();return 1
    finally:lock.unlink();log.close()


if __name__=='__main__':sys.exit(main())
