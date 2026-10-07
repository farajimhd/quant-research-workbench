"""Full overlap profiles keep one owner, warm references and audited scope."""
import json
from pathlib import Path
import pytest
from research.vectorized_backtest.v5.torch_backtest import profile_concurrency as profile
from research.vectorized_backtest.v5.torch_backtest.runtime import write_json


def test_serial_and_pipeline_are_paired_and_only_warm_totals_are_compared(tmp_path,monkeypatch):
    spec=tmp_path/'sessions.json';spec.write_text('{}');calls=[];audits=[]
    def run(argv,emit_hook):
        output=Path(argv[argv.index('--output')+1]);index=len(calls);calls.append(argv)
        write_json(output/'measurements.json',[dict(status='measured_full_session',elapsed_seconds=[100.,90.,80.,70.][index],timing_totals={})])
        emit_hook(dict(status='profile_complete',stage='done'))
        return 0
    monkeypatch.setattr(profile.profile_staged,'main',run)
    monkeypatch.setattr(profile,'compare',lambda a,b:audits.append((a,b)) or dict(actual_fills_exact=True))
    root=tmp_path/'job'
    assert profile.main(['--sessions',str(spec),'--output',str(root)])==0
    assert len(calls)==4 and len(audits)==2
    assert all('--no-rule-prefetch' in v for v in calls[:2])
    assert all('--rule-prefetch' in v for v in calls[2:])
    assert all('--profile-seconds' in v and '19800' in v for v in calls)
    assert all(a.parent.parent.name=='serial_warm' for a,b in audits)
    report=json.loads((root/'report.json').read_text())
    assert report['warm_total_improvement_percent']==pytest.approx(100*20/90)
    assert not report['validation_opened'] and not (root/'owner.lock').exists()
    with pytest.raises(ValueError,match='Immutable'):profile.main(['--sessions',str(spec),'--output',str(root)])


def test_incomplete_measurement_cannot_be_accepted(tmp_path,monkeypatch):
    spec=tmp_path/'sessions.json';spec.write_text('{}')
    def run(argv,emit_hook):
        output=Path(argv[argv.index('--output')+1])
        write_json(output/'measurements.json',[dict(status='memory_limit')]);return 0
    monkeypatch.setattr(profile.profile_staged,'main',run)
    root=tmp_path/'failed'
    with pytest.raises(ValueError,match='Complete full-session'):
        profile.main(['--sessions',str(spec),'--output',str(root)])
    assert not (root/'report.json').exists() and not (root/'owner.lock').exists()
    assert json.loads((root/'failure.json').read_text())['status']=='failed'
