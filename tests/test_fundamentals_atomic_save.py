import json
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts.run_fundamentals import save


def test_atomic_save_retries_reader_sharing_violation(tmp_path):
    target=tmp_path/'status.json';target.write_text('{"old":true}')
    replace=Path.replace;calls=[]
    def busy(source,destination):
        calls.append(source)
        if len(calls)<3:
            assert json.loads(target.read_text())=={'old':True}
            raise PermissionError('reader holds target')
        return replace(source,destination)
    with patch.object(Path,'replace',busy),patch('scripts.run_fundamentals.time.sleep'):
        save(target,{'new':True})
    assert len(calls)==3 and json.loads(target.read_text())=={'new':True}
    assert not list(tmp_path.glob('*.tmp'))


def test_atomic_save_permanent_denial_preserves_previous_receipt(tmp_path):
    target=tmp_path/'receipt.json';target.write_text('{"verified":true}')
    with patch.object(Path,'replace',side_effect=PermissionError('denied')) as replace,patch('scripts.run_fundamentals.time.sleep'):
        with pytest.raises(PermissionError):save(target,{'replacement':True})
    assert replace.call_count==21 and json.loads(target.read_text())=={'verified':True}
    assert not list(tmp_path.glob('*.tmp'))
