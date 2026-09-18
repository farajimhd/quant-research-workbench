import io
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from rich.console import Console
from research.level_book.v7 import filtered_campaign as f


def fixture_catalog(root):
    parent=dict(plan_hash='parent',start='2025-01-01',end='2026-08-21',rows=[
        dict(ticker='TEST',status='queued',directory='TEST',coverage=dict(days=3)),
        dict(ticker='GAP',status='deferred',reason='unresolved identity')])
    return SimpleNamespace(root=root,fingerprint='catalog',plans=[(root/'main',parent)],
        by_ticker={'TEST':[(root/'main',parent,parent['rows'][0])]})


def test_plan_pins_consumer_identity_and_preserves_deferred(tmp_path,monkeypatch):
    monkeypatch.setattr(f,'Catalog',fixture_catalog)
    plan=f.make_plan(tmp_path)
    assert len(plan['rows'])==2
    ready,gap=plan['rows']
    assert ready['before']=='2026-08-22' and ready['sessions']==3
    assert gap['state']=='deferred' and gap['reason']=='unresolved identity'
    f.c.write(tmp_path/'plan.json',plan)
    assert f.checked_plan(tmp_path)==plan
    monkeypatch.setattr(f,'kernel',lambda:{'software':'different'})
    with pytest.raises(ValueError,match='consumer contract'):f.checked_plan(tmp_path)


def test_stopped_campaign_never_starts_ticker_writer(tmp_path,monkeypatch):
    parent=dict(plan_hash='parent',rows=[dict(ticker='TEST')])
    parent['plan_hash']=f.c.digest({k:v for k,v in parent.items() if k!='plan_hash'})
    f.c.write(tmp_path/'main/plan.json',parent)
    monkeypatch.setattr(f,'checked_plan',lambda _:None)
    monkeypatch.setattr(f,'successor',lambda *args:(tmp_path/'out',dict(plan_hash='out')))
    folder=tmp_path/'job';folder.mkdir();(folder/'STOP').touch()
    row=dict(parent='main',parent_hash=parent['plan_hash'],ticker='TEST',output='out',plan_hash='out')
    assert f.execute(tmp_path,folder,row)['state']=='interrupted'
    assert not (tmp_path/'out/plan.json').exists()


@pytest.mark.parametrize('width,height',[(80,24),(150,40)])
@pytest.mark.parametrize('status',['running','stopping','failed','complete_with_gaps'])
def test_terminal_rows_are_paged_and_bounded(width,height,status):
    now=datetime.now(timezone.utc).isoformat()
    state=dict(state=status,started_epoch=1,updated_at=now,workers=56,sessions_total=1000,sessions_completed=10,
        rows={'TEST':dict(state='active',slot=0),'BAD':dict(state='failed')},
        progress={'TEST':dict(completed=10,total=400,resumed=2,stage='MLE fitting',session='2026-08-21',updated_at=now)})
    out=io.StringIO();console=Console(file=out,width=width,height=height,color_system=None)
    console.print(f.render(state,width,height))
    lines=out.getvalue().splitlines()
    assert len(lines)<=height and max(map(len,lines))<=width
    assert 'Failed 1' in out.getvalue() and 'page 1/' in out.getvalue()
