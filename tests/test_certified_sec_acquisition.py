from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from pipelines.certified_research import sec_acquisition as sec
from pipelines.certified_research.contracts import CampaignConfig

CONFIG=CampaignConfig(date(2026,1,2),date(2026,1,2))
PATHS=SimpleNamespace(runtime=Path('D:/TradingML/runtimes/certified-research'))
READY=dict(ready=True,gaps={},coverage_hash='a'*64)
MISSING=dict(ready=False,gaps={'sec_daily_archive':(('a','b'),)})


def test_sec_command_is_complete_scoped_and_exclusive_end_is_correct():
    command=sec.command(PATHS,CONFIG,date(2025,1,1),'/mnt/d/market-data/runtime/sec')
    assert command[command.index('--end-date')+1]=='2026-01-04'
    assert command[command.index('--context-start-date')+1]=='2025-01-01'
    assert '--execute' in command and '--runtime-root-win' in command
    assert not any(flag in command for flag in ('--start-stage','--finalize-only','--limit-days','--continue-on-error'))


@pytest.mark.parametrize('ready,optin,alias,expected_calls',[
    (True,False,None,0),(False,False,None,0),(False,True,None,0),
    (False,True,'/mnt/d/market-data/runtime/sec',1),
])
def test_coverage_reuse_and_missing_acquisition_require_exact_optins(ready,optin,alias,expected_calls):
    query=Mock(return_value=[dict(n=1)]);claim=Mock();runner=Mock()
    snapshots=[READY] if ready else [MISSING,READY]
    with patch.object(sec.sec_lineage,'qualify') as qualify,\
         patch.object(sec.sec_coverage,'inventory',side_effect=snapshots):
        if not ready and (not optin or alias is None):
            with pytest.raises(ValueError):
                sec.prepare(query,PATHS,CONFIG,claim,Event(),fundamental_start=date(2025,1,1),
                    acquire_missing=optin,runtime_root_ch=alias,runner=runner)
        else:
            result=sec.prepare(query,PATHS,CONFIG,claim,Event(),fundamental_start=date(2025,1,1),
                acquire_missing=optin,runtime_root_ch=alias,runner=runner)
            assert result['status']==('reused' if ready else 'prepared')
    assert runner.call_count==expected_calls
    assert qualify.call_count==1+expected_calls


def test_successful_child_exit_cannot_replace_independent_full_qualification():
    with patch.object(sec.sec_lineage,'qualify'),patch.object(sec.sec_coverage,'inventory',return_value=MISSING),\
         pytest.raises(ValueError,match='every required'):
        sec.prepare(Mock(return_value=[dict(n=1)]),PATHS,CONFIG,Mock(),Event(),
            fundamental_start=date(2025,1,1),acquire_missing=True,
            runtime_root_ch='/mnt/d/market-data/runtime/sec',runner=Mock())


def test_cli_sec_options_cannot_be_used_by_frozen_execution():
    from scripts.prepare_certified_research import parse_args
    with pytest.raises(SystemExit):parse_args(['prepare-sources','--end-date','2026-01-02','--acquire-sec-missing'])
    with pytest.raises(SystemExit):parse_args(['resume','--dataset-id','00000000-0000-0000-0000-000000000001',
        '--capacity-profile','pilot.json','--fundamental-start-date','2025-01-01','--acquire-sec-missing'])
