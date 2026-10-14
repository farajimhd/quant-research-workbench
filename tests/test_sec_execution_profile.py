import ast,json,os,subprocess,sys
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from pipelines.sec.edgar import sec_historical_gap_fill as fill,sec_execution_profile as profile
from research.mlops.clickhouse import ClickHouseHttpClient


def args(root,*extra):
    return fill.parse_args(['--start-date','2026-01-02','--end-date','2026-01-03',
        '--runtime-root-win',str(root),'--context-start-date','2026-01-02',*extra])


def test_scoped_chain_contains_every_stage_and_only_supported_child_options(tmp_path):
    config=args(tmp_path,'--execution-workers','2','--execution-query-threads','1',
        '--execution-query-memory-bytes',str(256*1024**2))
    plans=profile.commands(config,fill.build_commands(config,tmp_path/'logs'))
    from pipelines.sec.edgar.sec_acquisition_contract import ALL
    assert {plan.stage for plan in plans}==set(ALL) and len(plans)==21
    for plan in plans:
        child=Path(plan.command[1]);tree=ast.parse(child.read_text(encoding='utf-8-sig'))
        supported={arg.value for node in ast.walk(tree) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument'
            for arg in node.args if isinstance(arg,ast.Constant) and isinstance(arg.value,str)}
        for index,flag in enumerate(plan.command):
            if flag in ('--storage-policy','--max-threads','--max-memory-usage','--insert-max-threads','--insert-max-memory-usage'):
                assert flag in supported
            if flag.endswith('output-root-win') or flag=='--parts-root-win':
                assert Path(plan.command[index+1]).is_relative_to(tmp_path)
    context=next(plan for plan in plans if plan.stage=='sec-context-build').command
    assert context[context.index('--start-date')+1]=='2026-01-02'
    assert context[context.index('--max-memory-usage')+1]==str(256*1024**2)
    assert config.context_database=='market_sip_compact' # Existing standalone routing is preserved.


@pytest.mark.parametrize('extra',[
    ['--execution-workers','0'],['--execution-query-threads','0'],
    ['--execution-query-memory-bytes','1'],['--execution-global-memory-bytes','1'],
])
def test_invalid_scoped_resources_fail_before_writes(tmp_path,extra):
    with pytest.raises(SystemExit):args(tmp_path,*extra)
    assert not list(tmp_path.iterdir())


def test_actual_parent_dry_run_publishes_complete_scoped_plan_without_children(tmp_path):
    command=[sys.executable,'-B',str(Path(fill.__file__)),
        '--start-date','2026-01-02','--end-date','2026-01-03','--runtime-root-win',str(tmp_path),
        '--context-start-date','2026-01-02','--execution-workers','2','--progress-layout','text']
    result=subprocess.run(command,capture_output=True,text=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),timeout=30)
    assert result.returncode==0,result.stderr
    manifests=list(tmp_path.rglob('sec_historical_gap_fill_manifest.json'))
    assert len(manifests)==1
    manifest=json.loads(manifests[0].read_text())
    assert not manifest['execute'] and len(manifest['commands'])==21
    assert manifest['execution_profile']['workers']==2
    assert not list(tmp_path.rglob('sec_historical_gap_fill_results.jsonl'))


def test_query_defaults_preserve_readonly_and_lower_limits(monkeypatch):
    monkeypatch.setenv('SEC_EXECUTION_MAX_THREADS','2');monkeypatch.setenv('SEC_EXECUTION_MAX_MEMORY_BYTES',str(256*1024**2))
    client=ClickHouseHttpClient('http://localhost','', '',default_query_params={'readonly':1,'max_threads':1,'max_memory_usage':'128M'})
    assert client.default_query_params=={'readonly':'1','max_threads':'1','max_memory_usage':str(128*1024**2)}
    client.close()
    client=ClickHouseHttpClient('http://localhost','','',default_query_params={'max_memory_usage':0,'max_threads':0})
    assert client.default_query_params['max_threads']=='2' and int(client.default_query_params['max_memory_usage'])==256*1024**2
    client.close()


def test_output_failure_stops_real_owned_child(tmp_path):
    command=fill.StageCommand('fixture',[sys.executable,'-B','-u','-c',
        'import time; print("ready",flush=True); time.sleep(60)'],tmp_path/'child.log',False)
    progress=Mock(enabled=True);progress.log_line.side_effect=RuntimeError('fixture output failure')
    processes=[];real_popen=subprocess.Popen
    def spawn(*args,**kwargs):
        child=real_popen(*args,**kwargs);processes.append(child);return child
    with patch.object(fill.subprocess,'Popen',side_effect=spawn),pytest.raises(RuntimeError,match='fixture output failure'):
        fill.run_stage(command,progress=progress)
    assert len(processes)==1 and processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_runtime_access_rejects_unreadable_mapping_before_creating_files(tmp_path):
    config=args(tmp_path)
    client=Mock();client.execute.return_value=json.dumps(dict(value='/mnt/d/market-data/'))
    with pytest.raises(ValueError,match='user_files_path'):
        profile.preflight_runtime_access(config.execution_profile,client)
    assert client.execute.call_count==1 and not list(tmp_path.iterdir())


def test_runtime_alias_requires_exact_probe_and_cleans_probe(tmp_path):
    config=args(tmp_path,'--runtime-root-ch','/mnt/d/market-data/runtime-alias')
    def read(sql):
        if 'system.server_settings' in sql:return json.dumps(dict(value='/mnt/d/market-data/'))
        probe=next(tmp_path.glob('sec-runtime-probe-*.bin'))
        import hashlib
        return json.dumps(dict(proof=hashlib.sha256(probe.read_bytes()).hexdigest()))
    client=Mock();client.execute.side_effect=read
    evidence=profile.preflight_runtime_access(config.execution_profile,client)
    assert len(evidence['proof_hash'])==64 and not list(tmp_path.iterdir())
    client.execute.side_effect=[json.dumps(dict(value='/mnt/d/market-data/')),json.dumps(dict(proof='0'*64))]
    with pytest.raises(ValueError,match='exactly'):profile.preflight_runtime_access(config.execution_profile,client)
    assert not list(tmp_path.iterdir())
