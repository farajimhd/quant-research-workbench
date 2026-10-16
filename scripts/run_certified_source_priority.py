"""Serialize a bounded December source pass before resuming the existing owner.

Run on the workstation under a persistent SSH session. Never kills a process,
changes ingestion code, cancels a mutation, or overwrites earlier worker logs.
"""
from __future__ import annotations
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
from contextlib import closing
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

PRIORITY_DAYS=tuple(date(2025,12,d) for d in (26,29,30,31))


def prepare_priority(parsed,config,paths,query,writer,claim,stop,prepare):
    """December is an explicit source context, never a pre-2026 target."""
    return prepare(query,paths,config,claim,stop,writer_query=writer,
        context_start=PRIORITY_DAYS[0],sessions=PRIORITY_DAYS,recover_retained_reporting=True,
        staging_reserve_bytes=parsed.staging_reserve_gib*1024**3,
        staging_reserve_fraction=parsed.staging_reserve_fraction,
        staging_chunk_bytes=parsed.staging_chunk_gib*1024**3)


def execute_worker(path,*,inspect_only=False,verify_only=False):
    request=json.loads(Path(path).read_text())
    sys.path.insert(0,request['source_code'])
    # Existing imports may load environment defaults. Pin transport afterward,
    # exactly as the original immutable worker did.
    from scripts import prepare_certified_research as preparation
    from pipelines.market_sip.flatfiles.download_update_events import load_env_files
    parsed=preparation.parse_args(request['arguments'])
    load_env_files([parsed.env_file],verbose=False)
    os.environ['QMD_CLICKHOUSE_URL']=request['endpoint']
    if request['name']=='original_resume' and not inspect_only and not verify_only:return preparation.main(request['arguments'])
    from scripts import build_market_day as builder
    from pipelines.certified_research import acquisition
    from pipelines.certified_research.publication import SourceClaim
    from src.trading_runtime.keeper_session import open_workstation_keeper_session
    from threading import Event
    import signal
    config=preparation.campaign_config(parsed,parsed.end_date)
    from types import SimpleNamespace
    options=SimpleNamespace(env_file=parsed.env_file,max_threads=config.query_threads,
        max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=config.query_timeout_seconds)
    paths=acquisition.IngestionPaths(parsed.env_file,parsed.runtime_root,parsed.cache_root,
        parsed.archive_root,parsed.clickhouse_cache_root)
    if inspect_only or verify_only:
        from pipelines.certified_research import source
        with closing(builder.Client(options,persistent=True,readonly=True)) as client:
            if client.secrets[0]!=request['endpoint']:raise ValueError('Worker effective endpoint differs')
            selected=PRIORITY_DAYS if request['name']=='priority' else None
            inventory=source.inventory(client.query,PRIORITY_DAYS[0] if selected else config.start,config.end,sessions=selected)
            reporting=source.reporting_inventory(client.query,inventory)
            if verify_only:
                fingerprint=source.require_ready(client.query,inventory,reporting)
                print(json.dumps(dict(source_hash=fingerprint,sessions=list(map(str,inventory['expected'])),
                    canonical_hash=inventory['content_hash'],reporting_hash=reporting['content_hash'])))
                return 0
            print(json.dumps(dict(config_start=str(config.start),config_end=str(config.end),
                sessions=list(map(str,inventory['expected'])),canonical_missing=list(map(str,inventory['missing'])),
                canonical_incomplete=list(map(str,inventory['incomplete'])),reporting_missing=list(map(str,reporting['missing'])),
                overlapping_mutation=reporting['mutating'],effective_endpoint_verified=True)))
        return 0
    stop=Event();signal.signal(signal.SIGINT,lambda *unused:stop.set())
    print('Priority source context | '+','.join(map(str,PRIORITY_DAYS))+' | original 2026 target retained',flush=True)
    with closing(builder.Client(options,persistent=True)) as client,acquisition.stop_marker(stop,parsed.stop_file),closing(open_workstation_keeper_session()) as keeper:
        claim=SourceClaim(keeper.client)
        try:
            prepare_priority(parsed,config,paths,lambda sql:client.query(sql+' SETTINGS readonly=1'),
                client.query,claim,stop,acquisition.prepare_sources)
        finally:claim.close()
    return 0


def stamp():return datetime.now(timezone.utc).isoformat()


def write_json(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2,default=str),encoding='utf-8')
    temporary.replace(path)


def process(pid):
    if type(pid) is not int or pid<=0:raise ValueError('Invalid owned PID')
    command=("$ProgressPreference='SilentlyContinue'; Get-CimInstance Win32_Process -Filter "
        f"'ProcessId={pid}' | Select-Object ProcessId,CommandLine,CreationDate | ConvertTo-Json -Compress")
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True,text=True,check=True,timeout=20)
    return json.loads(result.stdout) if result.stdout.strip() else None


def option(arguments,name):
    if arguments.count(name)!=1:raise ValueError('Missing or competing source option: '+name)
    return arguments[arguments.index(name)+1]


def replace_option(arguments,name,value):
    result=list(arguments);option(result,name);result[result.index(name)+1]=str(value);return result


def validate_arguments(arguments):
    if arguments[0]!='prepare-sources':raise ValueError('Existing worker is not source preparation')
    expected={'--start-date':'2026-01-01','--end-date':'2026-10-02',
        '--query-threads':'8','--query-memory-gib':'8','--global-memory-gib':'16',
        '--workers':'2','--staging-reserve-gib':'256','--staging-reserve-fraction':'0.10',
        '--staging-chunk-gib':'256'}
    if any(option(arguments,k)!=v for k,v in expected.items()):
        raise ValueError('Existing source bounds or resource envelope differ')
    if '--recover-retained-reporting' not in arguments or any(k in arguments for k in
            ('--acquire-missing','--stage-archive','--acquire-sec-missing','--identity-point','--repair-retained-symbols')):
        raise ValueError('Only retained reporting preparation is authorized')


def sequence(operations):
    """Once drained, always attempt original receipt-based resumption."""
    operations.drain()
    try:
        operations.priority()
    except Exception as error:
        operations.priority_error(error)
    finally:
        operations.resume_original()


class Controller:
    def __init__(self,args,state):
        self.args=args;self.state=state;self.run=args.run_dir

    def save(self,phase=None,**values):
        if phase:self.state['phase']=phase
        self.state.update(values,updated_at=stamp(),controller_pid=os.getpid())
        write_json(self.run/'status.json',self.state)
        if phase:print(f'Source handoff | {phase} | status: {self.run / "status.json"}',flush=True)

    def verify_release(self):
        from src.trading_runtime.keeper_session import open_workstation_keeper_session
        from pipelines.certified_research.publication import SourceClaim
        with closing(open_workstation_keeper_session()) as keeper:
            claim=SourceClaim(keeper.client)
            try:claim.check()
            finally:claim.close()

    def drain(self):
        if self.state.get('drained'):return
        original=self.state['original'];pid=original['pid'];identity=process(pid)
        if identity and identity!=self.state['original_process']:
            raise RuntimeError('Original PID identity changed; stop control is not authorized')
        marker=Path(option(original['arguments'],'--stop-file'))
        if not marker.exists():
            with marker.open('x',encoding='utf-8') as stream:
                stream.write('Serialized December priority handoff '+str(self.run)+'\n')
        self.save('waiting_original_drain',stop_requested_at=self.state.get('stop_requested_at',stamp()))
        while process(pid) is not None:
            self.save();time.sleep(5)
        previous=self.state.get('previous_handoff')
        if previous:
            while process(self.state['previous_controller_pid']) is not None:self.save();time.sleep(5)
            terminal=json.loads((Path(previous)/'status.json').read_text())
            valid=(terminal.get('worker',{}).get('pid')==pid and terminal.get('phase') in ('complete','failed'))
        else:
            terminal=json.loads((self.args.original_run/'status.json').read_text())
            valid=(terminal.get('pid')==pid and terminal.get('status') in ('complete','interrupted','failed'))
        if not valid:
            raise RuntimeError('Original process ended without a matching terminal receipt')
        self.verify_release()
        # Preserve the exact stop request and terminal receipt before allowing
        # the original immutable code to run again with the same arguments.
        write_json(self.run/'original_terminal.json',terminal)
        marker.rename(self.run/'original_stop.request')
        self.save('original_drained',drained=True,original_terminal_status=terminal.get('status',terminal.get('phase')))

    def wait_child(self,name,arguments):
        worker=self.state.get('worker')
        if worker and worker['name']==name:
            while True:
                identity=process(worker['pid'])
                if identity is None:return None # Receipt verification supplies the result after adoption.
                if identity!=worker['identity']:raise RuntimeError('Adopted PID identity changed')
                self.save();time.sleep(5)
        index=1
        while (self.run/f'{name}.{index}.stdout.log').exists():index+=1
        out=self.run/f'{name}.{index}.stdout.log';err=self.run/f'{name}.{index}.stderr.log'
        request=self.run/f'{name}.{index}.request.json'
        code=self.args.priority_source_code if name=='priority' else self.args.source_code
        write_json(request,dict(name=name,source_code=str(code),endpoint=self.args.endpoint,arguments=arguments))
        command=[sys.executable,'-B','-u',str(Path(__file__).resolve()),'--execute-worker',str(request)]
        self.save(name+'_starting',worker=None)
        with out.open('x',encoding='utf-8') as stdout,err.open('x',encoding='utf-8') as stderr:
            child=subprocess.Popen(command,cwd=self.args.source_code,stdout=stdout,stderr=stderr,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS,
                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',
                    CUDA_VISIBLE_DEVICES='-1',HIP_VISIBLE_DEVICES='-1'))
            identity=process(child.pid)
            self.save(name+'_running',worker=dict(name=name,pid=child.pid,identity=identity,
                stdout=str(out),stderr=str(err),started_at=stamp()))
            while child.poll() is None:self.save();time.sleep(5)
            return child.wait()

    def verify(self,start,end):
        name='priority' if start==PRIORITY_DAYS[0] else 'original_resume'
        request=self.run/(name+'.verify.request.json')
        code=self.args.priority_source_code if name=='priority' else self.args.source_code
        write_json(request,dict(name=name,source_code=str(code),endpoint=self.args.endpoint,
            arguments=self.state['original']['arguments']))
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--verify-worker',str(request)],
            capture_output=True,text=True,check=True,timeout=900,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='-1',HIP_VISIBLE_DEVICES='-1'))
        return json.loads(result.stdout)

    def priority(self):
        if self.state.get('priority_finished'):return
        arguments=self.state['original']['arguments']
        arguments=replace_option(arguments,'--stop-file',self.run/'priority.stop.request')
        code=self.wait_child('priority',arguments)
        if code not in (0,None):raise RuntimeError('December source worker failed; inspect retained stderr')
        receipt=self.verify(date(2025,12,26),date(2025,12,31))
        self.save('priority_complete',priority_finished=True,priority_status='complete',priority_receipt=receipt,worker=None)

    def priority_error(self,error):
        self.save('priority_failed',priority_finished=True,priority_status='failed',priority_error_type=type(error).__name__,worker=None)

    def resume_original(self):
        worker=self.state.get('worker')
        if not worker or worker['name']!='original_resume':self.verify_release()
        code=self.wait_child('original_resume',self.state['original']['arguments'])
        if code not in (0,None):raise RuntimeError('Original source resume failed; inspect retained stderr')
        receipt=self.verify(date(2026,1,1),date(2026,10,2))
        self.save('complete',original_status='complete',original_receipt=receipt,worker=None,finished_at=stamp())


def main(argv=None):
    if argv is None:argv=sys.argv[1:]
    if len(argv)==2 and argv[0]=='--execute-worker':return execute_worker(argv[1])
    if len(argv)==2 and argv[0]=='--inspect-worker':return execute_worker(argv[1],inspect_only=True)
    if len(argv)==2 and argv[0]=='--verify-worker':return execute_worker(argv[1],verify_only=True)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-code',type=Path,required=True)
    p.add_argument('--priority-source-code',type=Path,required=True)
    p.add_argument('--original-run',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--endpoint',required=True)
    p.add_argument('--previous-handoff',type=Path)
    p.add_argument('--resume',action='store_true')
    a=p.parse_args(argv)
    if os.name!='nt':raise RuntimeError('Source handoff requires the Windows workstation')
    if not a.run_dir.parent.is_dir():raise ValueError('Required runtime parent unavailable')
    source_file=a.source_code/'scripts/prepare_certified_research.py'
    if not source_file.is_file():raise ValueError('Original immutable ingestion code unavailable')
    original_file=a.original_run/'run.py'
    original_hash=hashlib.sha256(original_file.read_bytes()).hexdigest()
    if a.resume:
        state=json.loads((a.run_dir/'status.json').read_text())
        if (state['source_code']!=str(a.source_code) or state['priority_source_code']!=str(a.priority_source_code)
                or state['original_run_hash']!=original_hash
                or state['endpoint']!=a.endpoint):
            raise ValueError('Handoff resume identity differs')
        if state['phase']=='complete':return 0
    else:
        if a.previous_handoff:
            previous=json.loads((a.previous_handoff/'status.json').read_text())
            if previous['phase']!='original_resume_running' or previous['worker']['name']!='original_resume':
                raise ValueError('Prior handoff is not running the original resumed worker')
            original=dict(previous['original'],pid=previous['worker']['pid'],status='running')
            identity=process(int(original['pid']))
            if (identity!=previous['worker']['identity'] or previous['source_code']!=str(a.source_code)
                    or previous['original_run_hash']!=original_hash):
                raise ValueError('Previous handoff worker identity differs')
        else:
            original=json.loads((a.original_run/'status.json').read_text())
            identity=process(int(original['pid']))
            if original['status']!='running' or not identity or str(original_file).replace('\\','/').lower() not in identity['CommandLine'].replace('\\','/').lower():
                raise ValueError('Original live source worker identity is unproven')
        validate_arguments(original['arguments'])
        if Path(option(original['arguments'],'--stop-file')).exists():raise ValueError('Original stop marker already exists')
        a.run_dir.mkdir()
        state=dict(version='certified-source-priority-v1',original=original,original_process=identity,
            source_code=str(a.source_code),original_run_hash=original_hash,endpoint=a.endpoint,
            priority_source_code=str(a.priority_source_code),started_at=stamp(),phase='ready',gpu=False)
        if a.previous_handoff:state.update(previous_handoff=str(a.previous_handoff),previous_controller_pid=previous['controller_pid'])
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    sys.path.insert(0,str(a.source_code))
    import msvcrt
    with (a.run_dir/'controller.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        controller=Controller(a,state)
        controller.save()
        try:sequence(controller)
        except BaseException as error:
            controller.save('failed',error_type=type(error).__name__)
            raise
    return 0 if state.get('priority_status')=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
