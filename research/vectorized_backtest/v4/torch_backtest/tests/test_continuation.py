import json
from pathlib import Path

import pytest

from research.vectorized_backtest.v4.torch_backtest.continuation import import_first_generation
from research.vectorized_backtest.v4.torch_backtest.runtime import file_hash


def parent_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr('psutil.pid_exists', lambda pid: False)
    parent = tmp_path / 'parent'
    parent.mkdir()
    keys = ('sessions', 'split', 'objective', 'financial_settings', 'features',
            'searchable_feature_indices', 'policy_coordinates', 'program_maximum_nodes',
            'stages', 'arguments')
    identity = {key: key for key in keys}
    identity['code_hash'] = 'old'
    (parent / 'identity.json').write_text(json.dumps(identity))
    (parent / 'status.json').write_text(json.dumps(dict(status='interrupted', worker_pid=123)))
    (parent / 'exit.json').write_text(json.dumps(dict(exit=-1)))
    session = parent / 'generation_000' / 'session_000'
    session.mkdir(parents=True)
    (session / 'fills.pt').write_bytes(b'original financial ledger')
    (session / 'receipt.json').write_text(json.dumps(dict(
        population_sha256='population', ledger_sha256=file_hash(session / 'fills.pt'))))
    identity['code_hash'] = 'new'
    return parent, tmp_path / 'continued', identity


def test_revision_preserves_receipts_and_records_source_lineage(tmp_path, monkeypatch):
    parent, output, identity = parent_campaign(tmp_path, monkeypatch)
    before = file_hash(parent / 'identity.json')
    import_first_generation(parent, output, identity, 'population')
    record = json.loads((output / 'continuation.json').read_text())
    assert record['previous_code_hash'] == 'old'
    assert record['code_hash'] == 'new'
    assert record['parent_identity_sha256'] == before
    assert file_hash(parent / 'identity.json') == before
    for name in ('receipt.json', 'fills.pt'):
        relative = Path('generation_000/session_000') / name
        assert file_hash(parent / relative) == file_hash(output / relative)


@pytest.mark.parametrize('failure', ['contract', 'population', 'live', 'ledger'])
def test_revision_rejects_unsafe_reuse_before_copy(tmp_path, monkeypatch, failure):
    parent, output, identity = parent_campaign(tmp_path, monkeypatch)
    population = 'population'
    if failure == 'contract':
        identity['objective'] = 'different'
    elif failure == 'population':
        population = 'different'
    elif failure == 'live':
        monkeypatch.setattr('psutil.pid_exists', lambda pid: True)
    else:
        (parent / 'generation_000/session_000/fills.pt').write_bytes(b'changed')
    with pytest.raises(ValueError):
        import_first_generation(parent, output, identity, population)
    assert not output.exists()
