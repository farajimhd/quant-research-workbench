import json
from datetime import date

import pytest

from scripts import run_strategy_222_refinement as launcher
from scripts import summarize_strategy_222_refinement as report


def test_explicit_session_reaches_launcher_without_frozen_window_lookup(monkeypatch, tmp_path):
    observed=[]
    async def capture(args):
        observed.append(args)
    monkeypatch.setattr(launcher,'run',capture)
    monkeypatch.setattr('sys.argv',['research','--runtime',str(tmp_path),'--session-date','2026-08-19',
        '--symbol','TEST','--end','10:00:00','--variants','corrected-baseline'])
    launcher.main()
    assert len(observed)==1 and observed[0].session_date==date(2026,8,19)


def test_other_day_cannot_reuse_original_position_windows(monkeypatch,tmp_path):
    monkeypatch.setattr('sys.argv',['research','--runtime',str(tmp_path),
        '--session-date','2026-08-19','--position-symbols','TEST'])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code==2


def test_summary_checks_actual_completed_session_not_just_manifest_label():
    summary=dict(status='completed',run_id='run',session_date='2026-08-21',
                 session_end='2026-08-21T06:40:00-07:00')
    assert report.trial_session({},summary,'run')=='2026-08-21'
    with pytest.raises(ValueError,match='session differs'):
        report.trial_session(dict(session_date='2026-08-19'),summary,'run')
    with pytest.raises(ValueError,match='session differs'):
        report.trial_session({},dict(summary,status='running'),'run')
    with pytest.raises(ValueError,match='session differs'):
        report.trial_session({},dict(summary,session_end='2026-08-21T09:40:00'),'run')


def test_trades_only_report_explicitly_omits_hindsight_coverage(monkeypatch,tmp_path):
    def forbidden(*args):
        raise AssertionError('Original-day labels must not be loaded')
    monkeypatch.setattr(report,'compare_positions',forbidden)
    report.summarize(tmp_path,audit_original_positions=False)
    result=json.loads((tmp_path/'position-comparison.json').read_text())
    assert result['status']=='not_requested' and result['cases']==[]


def test_historical_watchlist_uses_empty_production_ticker_filter(monkeypatch,tmp_path):
    observed=[]
    async def capture(args):
        observed.append(args)
    monkeypatch.setattr(launcher,'run',capture)
    monkeypatch.setattr('sys.argv',['research','--runtime',str(tmp_path),'--session-date','2026-08-19',
        '--historical-watchlist-universe','--end','20:00:00','--variants','corrected-baseline'])
    launcher.main()
    assert observed[0].symbol=='WATCHLIST'
    assert launcher.replay_tickers(observed[0])==()
    assert observed[0].session_date==date(2026,8,19)


@pytest.mark.parametrize('extra',[
    ['--symbol','TEST','--end','20:00:00'],
    ['--portfolio-symbols','TEST','--end','20:00:00'],
    ['--position-symbols','TEST','--end','20:00:00'],
    [],
])
def test_historical_watchlist_rejects_ambiguous_scope(monkeypatch,tmp_path,extra):
    monkeypatch.setattr('sys.argv',['research','--runtime',str(tmp_path),
        '--historical-watchlist-universe',*extra])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code==2
