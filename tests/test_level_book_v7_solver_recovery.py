from copy import deepcopy
import sys
import pytest
from research.level_book.v7 import campaign as c
from scripts.prepare_level_book_v7_solver_recovery import prepare,verify_parent,OLD_SOLVER_HASH,SOLVER_FILE


def fixture(parent):
    hashes=c.hashes();hashes[SOLVER_FILE]=OLD_SOLVER_HASH
    plan=dict(version=c.VERSION,source_files=hashes,software=dict(python=sys.version,numpy=c.np.__version__,scipy=c.scipy.__version__),
              rows=[dict(ticker='TEST',status='queued',reason='',coverage={'days':2})],rules=[])
    plan['plan_hash']=c.digest(plan)
    root=c.paths(parent,'TEST');days=[dict(source_date='2025-06-06'),dict(source_date='2025-06-09')]
    c.write(parent/'plan.json',plan)
    c.write(root/'source-plan.json',dict(plan_hash=plan['plan_hash'],days=days,splits=[]))
    c.write(root/'progress.json',dict(state='failed'))
    book=dict(ticker='TEST',session='2025-06-06');book['checkpoint_hash']=c.digest(book)
    c.write(root/'books/2025-06-06.json.gz',book)
    c.write(root/'receipts/2025-06-06.json',dict(state='complete',source_hash=c.source_hash(days[0],[]),parent_hash=None,checkpoint_hash=book['checkpoint_hash']))
    return plan


def test_recovery_preserves_original_and_resumes_partial_preparation(tmp_path,monkeypatch):
    parent=tmp_path/'parent';destination=tmp_path/'recovery';old=fixture(parent)
    before={str(p.relative_to(parent)):p.read_bytes() for p in parent.rglob('*') if p.is_file()}
    original=c.write
    def interrupted(path,value,**kwargs):
        if path==destination/'plan.json':raise OSError('interrupted before plan publication')
        return original(path,value,**kwargs)
    monkeypatch.setattr(c,'write',interrupted)
    with pytest.raises(OSError):prepare(parent,destination,'TEST')
    monkeypatch.setattr(c,'write',original)
    new=prepare(parent,destination,'TEST')
    assert new['parent_plan_hash']==old['plan_hash'] and new['plan_hash']!=old['plan_hash']
    assert new['inherited_prefix']['inherited_sessions']==['2025-06-06']
    assert prepare(parent,destination,'TEST')==new
    assert all((parent/path).read_bytes()==raw for path,raw in before.items())
    for sub in ('books/2025-06-06.json.gz','receipts/2025-06-06.json'):
        assert (c.paths(parent,'TEST')/sub).read_bytes()==(c.paths(destination,'TEST')/sub).read_bytes()


def test_recovery_rejects_changed_code_or_corrupt_prefix(tmp_path):
    parent=tmp_path/'parent';plan=fixture(parent)
    changed=deepcopy(plan);changed['source_files']['other.py']='different';changed.pop('plan_hash');changed['plan_hash']=c.digest(changed)
    with pytest.raises(ValueError,match='only the reviewed'):verify_parent(changed)
    path=c.paths(parent,'TEST')/'receipts/2025-06-06.json';receipt=c.read(path);receipt['parent_hash']='corrupt'
    c.write(path,receipt,immutable=False)
    with pytest.raises(ValueError,match='chain mismatch'):prepare(parent,tmp_path/'recovery','TEST')
