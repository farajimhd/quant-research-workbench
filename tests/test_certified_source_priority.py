from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import pytest
from scripts.run_certified_source_priority import sequence,Controller,validate_arguments,replace_option,prepare_priority,PRIORITY_DAYS
from pipelines.certified_research.contracts import CampaignConfig
from datetime import date


def test_original_resumes_even_when_december_preparation_fails():
    events=[]
    def priority():
        events.append('priority');raise RuntimeError('December evidence unavailable')
    ops=SimpleNamespace(drain=lambda:events.append('drain'),priority=priority,
        priority_error=lambda error:events.append('priority_failed'),resume_original=lambda:events.append('resume_original'))
    sequence(ops)
    assert events==['drain','priority','priority_failed','resume_original']


def test_unproven_original_drain_never_launches_second_writer():
    ops=Mock();ops.drain.side_effect=RuntimeError('Ownership uncertain')
    with pytest.raises(RuntimeError):sequence(ops)
    ops.priority.assert_not_called();ops.resume_original.assert_not_called()


def test_handoff_success_is_serial_and_original_bounds_remain_unchanged():
    events=[]
    ops=SimpleNamespace(**{name:lambda n=name:events.append(n) for name in ('drain','priority','resume_original')})
    sequence(ops)
    assert events==['drain','priority','resume_original']
    original=['prepare-sources','--start-date','2026-01-01','--end-date','2026-10-02']
    priority=replace_option(original,'--start-date','2025-12-26')
    assert original[2]=='2026-01-01' and priority[2]=='2025-12-26'


def test_adopted_original_owner_is_waited_for_without_probing_its_held_claim():
    ctl=Controller(SimpleNamespace(run_dir=Path('D:/TradingML/runtimes')),
        dict(worker={'name':'original_resume'},original={'arguments':[]}))
    ctl.verify_release=Mock();ctl.wait_child=Mock(return_value=0);ctl.verify=Mock(return_value={});ctl.save=Mock()
    ctl.resume_original()
    ctl.verify_release.assert_not_called();ctl.wait_child.assert_called_once_with('original_resume',[])


def test_limits_and_network_acquisition_fail_closed():
    arguments=['prepare-sources','--recover-retained-reporting']
    limits={'--start-date':'2026-01-01','--end-date':'2026-10-02','--query-threads':'8',
        '--query-memory-gib':'8','--global-memory-gib':'16','--workers':'2','--staging-reserve-gib':'256',
        '--staging-reserve-fraction':'0.10','--staging-chunk-gib':'256'}
    for k,v in limits.items():arguments.extend([k,v])
    validate_arguments(arguments)
    with pytest.raises(ValueError):validate_arguments(arguments+['--acquire-missing'])
    with pytest.raises(ValueError):validate_arguments(replace_option(arguments,'--query-threads','16'))


def test_priority_uses_2026_config_and_only_four_explicit_december_context_sessions():
    config=CampaignConfig(date(2026,1,1),date(2026,10,2),query_threads=8,query_memory_bytes=8*1024**3,
        workers=2,global_memory_bytes=16*1024**3)
    parsed=SimpleNamespace(staging_reserve_gib=256,staging_reserve_fraction=.10,staging_chunk_gib=256)
    prepare=Mock()
    prepare_priority(parsed,config,'paths','query','writer','claim','stop',prepare)
    args,kwargs=prepare.call_args
    assert args[2].start==date(2026,1,1)
    assert kwargs['sessions']==PRIORITY_DAYS==tuple(date(2025,12,d) for d in (26,29,30,31))
    assert kwargs['context_start']==date(2025,12,26)
    assert kwargs['staging_reserve_bytes']==256*1024**3
