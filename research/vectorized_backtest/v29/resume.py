"""Audited performance-only checkpoint continuation; never resample population."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,shutil,time
from pathlib import Path
import psutil
from .run_search import digest,source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,require_runtime,write_json
ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v29')


def checked(path,sha):
    path=Path(path)
    if file_hash(path)!=sha:raise ValueError('Qualification evidence changed: '+str(path))
    return json.loads(path.read_bytes())


def qualify_comparison(qualification,population):
    """Require actual full financial and captured gate/ratio matching evidence."""
    q=qualification
    if q['candidate_source_sha256']!=source_hash():raise ValueError('Qualified candidate source changed')
    records={name:checked(q[name],q[name+'_sha256']) for name in ('baseline_full','candidate_full','baseline_audit','candidate_audit')}
    expected={name:q['baseline_source_sha256'] if name.startswith('baseline') else q['candidate_source_sha256'] for name in records}
    fixture=checked(q['fixture'],q['fixture_sha256'])
    if fixture['population']!=population or digest(population)!=fixture['population_sha256']:raise ValueError('Qualification used another checkpoint population')
    n=len(population)
    for name,r in records.items():
        if r['status']!='complete' or r['source_sha256']!=expected[name] or r['fixture_sha256']!=q['fixture_sha256']:raise ValueError('Wrong matched engine/fixture')
        if name.endswith('full') and not r['full_session']:raise ValueError('Full-session financial qualification required')
        if name.endswith('audit') and (not r['audit'] or r['clocks']!=128):raise ValueError('Captured 128-clock gate/ratio qualification required')
        if r['candidates']<n or r['candidates']%n:raise ValueError('Diagnostic repeats changed candidate order')
        if name.startswith('candidate') and r['batch']!=q['batch']:raise ValueError('Batch differs from qualified candidate')
    for suffix in ('full','audit'):
        base=records['baseline_'+suffix]['cases'][-1]
        candidate=records['candidate_'+suffix]['cases'][-1]
        for field in ('financial_hashes',)+(('gate_ratio_hashes',) if suffix=='audit' else ()):
            reference=base[field][:n]
            for r in (records['baseline_'+suffix],records['candidate_'+suffix]):
                if r['cases'][-1][field]!=reference*(r['candidates']//n):raise ValueError('Exact matched results differ: '+field)
    tests=checked(q['tests_result'],q['tests_result_sha256'])
    if tests['exit_code']!=0 or tests['source_sha256']!=source_hash():raise ValueError('Current-source passing tests required')
    if file_hash(Path(tests['output']))!=tests['output_sha256']:raise ValueError('Real test output changed')
    if q['batch'] not in (512,1024,2048,4096):raise ValueError('Unqualified batch envelope')
    return records


def prepare(previous,output,qualification,day='2026-08-04'):
    """Copy sealed evidence, change only source/batch contract, retain state hash.

    The old campaign is never edited. The normal runner subsequently verifies
    the complete new contract, population/RNG, archives and every copied seal.
    """
    previous=Path(previous).resolve();output=Path(output).resolve();qpath=Path(qualification)
    runtime=ROOT.resolve()
    if not previous.is_relative_to(runtime/'campaigns') or not output.is_relative_to(runtime/'campaigns') or output.exists():raise ValueError('New V29 campaign path required')
    owner=json.loads((previous/'active.json').read_bytes())
    try:
        if abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01:raise RuntimeError('Previous optimizer still owns the GPU')
    except psutil.NoSuchProcess:pass
    pointer=json.loads((runtime/'active-campaign.json').read_bytes())
    if pointer!=owner:raise ValueError('Active pointer changed')
    if json.loads((previous/'exit.json').read_bytes())['exit_code']!=130 or list(previous.rglob('owner.lock')):raise ValueError('Sealed stop and released ownership required')
    old=previous/'local'/day;saved=json.loads((old/'checkpoint.json').read_bytes());state=saved['state'];contract=saved['contract']
    if digest(state)!=saved['state_sha256'] or not 0<state['completed']<contract['generations']:raise ValueError('Incomplete/finished or corrupt continuation state')
    if not (old/'STOP').exists():raise ValueError('Explicit sealed stop evidence required')
    q=json.loads(qpath.read_bytes())
    if contract['source_sha256']!=q['baseline_source_sha256']:raise ValueError('Baseline is not the stopped source')
    qualify_comparison(q,state['population'])
    for g in range(1,state['completed']+1):
        folder=old/f'generation-{g:04d}';seal=json.loads((folder/'complete.json').read_bytes())
        if file_hash(folder/'complete.json')!=state['seals'][str(g)]:raise ValueError('Generation seal changed')
        for name,key in ((day+'/receipt.json','receipt_sha256'),('scores.json','scores_sha256'),('births.json','births_sha256')):
            if file_hash(folder/name)!=seal[key]:raise ValueError('Sealed evidence changed')
    new=require_runtime(output/'local'/day)
    for g in range(1,state['completed']+1):shutil.copytree(old/f'generation-{g:04d}',new/f'generation-{g:04d}')
    shutil.copy2(old/'initialization.json',new/'initialization.json')
    shutil.copy2(old/'checkpoint.json',output/'predecessor-checkpoint.json')
    shutil.copy2(previous/'manifest.json',output/'predecessor-manifest.json')
    new_contract=dict(contract,source_sha256=source_hash(),batch_size=q['batch'])
    migrated=dict(saved,contract=new_contract)
    if migrated['state']!=state or migrated['state_sha256']!=saved['state_sha256']:raise AssertionError('Evolution state must remain exact')
    write_json(new/'checkpoint.json',migrated)
    proof=dict(version='v29-performance-resume-v1',epoch=time.time(),previous=str(previous),output=str(output),day=day,completed=state['completed'],resume_generation=state['completed']+1,
               previous_source_sha256=contract['source_sha256'],source_sha256=source_hash(),previous_batch=contract['batch_size'],batch=q['batch'],
               previous_checkpoint_sha256=file_hash(old/'checkpoint.json'),checkpoint_sha256=file_hash(new/'checkpoint.json'),state_sha256=saved['state_sha256'],
               population_sha256=digest(state['population']),rng_sha256=digest(state['rng']),qualification=str(qpath),qualification_sha256=file_hash(qpath),
               permitted_contract_changes=['source_sha256','batch_size'],initial_evaluations_repeated=False,result_cache_imported=False)
    write_json(output/'resume-proof.json',proof)
    write_json(new/'status.json',dict(status='resume_ready',day=day,completed_generations=state['completed'],generation=state['completed']+1,stage='Qualified exact checkpoint continuation'))
    return proof
