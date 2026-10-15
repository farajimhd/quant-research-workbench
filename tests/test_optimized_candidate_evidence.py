import json
from pathlib import Path

import pytest

from src.trading_runtime.optimized_candidate_evidence import (
    _digest, _bytes_hash, freeze_candidate_evidence, select_audit_shortlist, read_frozen_candidate_evidence,
)


def fixture(tmp_path):
    campaign = tmp_path / 'campaign'
    def write(relative, value):
        path = campaign / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        return _bytes_hash(raw)
    payloads = [{'rules': {'entry': 1}}, {'rules': {'entry': 2}}]
    write('training/generation-0001/population.json', {'population': payloads, 'ids': [11, 12]})
    write('training/generation-0001/ranking.json', [{'candidate': 2, 'serial': 12}, {'candidate': 1, 'serial': 11}])
    write('validation/generation-0001/ranking.json', [{'candidate': 1, 'serial': 12}, {'candidate': 2, 'serial': 11}])
    for role in ('training', 'validation'):
        day = '2026-01-01' if role == 'training' else '2026-01-02'
        write(role + '.json', {'days': [day]})
        token = _digest(payloads if role == 'training' else payloads[::-1])
        receipt = dict(population_sha256=token, full_session=True, split=role, day=day, validation_opened=role == 'validation',
            execution={'fee': 1}, metrics={'net_pnl': [4, 5], 'episode_profits': [[1, 3], [5]],
                'episode_listings': [[0, 1], [0]]})
        prefix = role + '/generation-0001/'
        receipt_hash = write(prefix + day + '/receipt.json', receipt)
        panel_hash = write(prefix + 'session-panel.npz', {'numeric': 'opaque sealed bytes'})
        write(prefix + 'complete.json', dict(population_sha256=token, session_receipts={day: receipt_hash},
            session_panel_sha256=panel_hash, training_days=['2026-01-01'], display_only=True, validation_feedback=False))
    return campaign


def freeze(tmp_path, campaign, **kwargs):
    return freeze_candidate_evidence(campaign, 1, [12], tmp_path / 'frozen', runtime_root=tmp_path, **kwargs)


def test_freeze_exact_source_bytes_and_validation_index(tmp_path):
    campaign = fixture(tmp_path)
    manifest = freeze(tmp_path, campaign)
    assert manifest['complete'] is True
    assert manifest['candidate_envelopes'][0]['candidate'] == 2
    assert manifest['sessions'][12]['training'][0]['metrics']['net_pnl'] == 5
    assert manifest['sessions'][12]['validation'][0]['metrics']['net_pnl'] == 4
    for name, expected_hash in manifest['sources'].items():
        assert (tmp_path / 'frozen' / name).read_bytes() == (campaign / name).read_bytes()
        assert _bytes_hash((campaign / name).read_bytes()) == expected_hash
    with pytest.raises(FileExistsError): freeze(tmp_path, campaign)


@pytest.mark.parametrize('mode', ['receipt', 'population', 'validation_index', 'missing', 'path', 'execution', 'ragged'])
def test_reject_corrupt_source_without_complete_output(tmp_path, mode):
    campaign = fixture(tmp_path)
    path = campaign / 'validation/generation-0001/2026-01-02/receipt.json'
    if mode in ('receipt', 'execution', 'ragged'):
        value = json.loads(path.read_text())
        if mode == 'receipt': value['full_session'] = False
        if mode == 'execution': value['execution'] = {'fee': 2}
        if mode == 'ragged': value['metrics']['episode_profits'] = [[1], [2]]
        path.write_text(json.dumps(value))
        # Preserve a valid seal hash to exercise semantic validation as well.
        seal_path = path.parent.parent / 'complete.json'
        seal = json.loads(seal_path.read_text())
        seal['session_receipts']['2026-01-02'] = _bytes_hash(path.read_bytes())
        seal_path.write_text(json.dumps(seal))
    if mode == 'population':
        path = campaign / 'training/generation-0001/population.json'
        value = json.loads(path.read_text()); value['ids'] = [12, 11]
        path.write_text(json.dumps(value))
    if mode == 'validation_index':
        (campaign / 'validation/generation-0001/ranking.json').write_text(json.dumps([
            {'candidate': 2, 'serial': 12}, {'candidate': 1, 'serial': 11}]))
    if mode == 'missing': path.unlink()
    if mode == 'path':
        path = campaign / 'training.json'; path.write_text(json.dumps({'days': ['../escape']}))
    with pytest.raises((ValueError, FileNotFoundError)):
        freeze(tmp_path, campaign)
    assert not (tmp_path / 'frozen/manifest.json').exists()


def test_bounds_and_runtime_destination(tmp_path):
    campaign = fixture(tmp_path)
    with pytest.raises(ValueError): freeze(tmp_path, campaign, max_file_bytes=1)
    with pytest.raises(ValueError): freeze(tmp_path, campaign, max_total_bytes=2)
    with pytest.raises(ValueError):
        freeze_candidate_evidence(campaign, 1, [12], tmp_path.parent / 'bad', runtime_root=tmp_path)


def test_shortlist_is_deterministic_diverse_and_not_profit_ranked():
    rows = [dict(serial=serial, effective_program_policy_hash=identity * 64,
        stage_capability_signature={'add': True}, profit=100 - serial)
        for serial, identity in [(3, 'b'), (2, 'a'), (1, 'a')]]
    assert [row['serial'] for row in select_audit_shortlist(rows, count=2)] == [1, 3]
    with pytest.raises(ValueError): select_audit_shortlist(rows, count=3)


def test_frozen_reader_recomputes_all_derived_values(tmp_path):
    campaign = fixture(tmp_path)
    freeze(tmp_path, campaign)
    target = tmp_path / 'frozen'
    manifest = read_frozen_candidate_evidence(target)
    assert manifest['provenance']['status'] == 'provenance_unverified'
    manifest['candidate_envelopes'][0]['program_hash'] = 'a' * 64
    raw = json.dumps(manifest).encode()
    (target / 'manifest.json').write_bytes(raw)
    (target / 'manifest.sha256').write_text(_bytes_hash(raw) + '\n')
    with pytest.raises(ValueError, match='differs from its sealed'):
        read_frozen_candidate_evidence(target)


def test_source_metadata_final_exit_and_recorded_sha(tmp_path):
    campaign = fixture(tmp_path)
    (campaign / 'active.json').write_text(json.dumps(dict(source_sha256='a' * 64,
        arguments=['--generations', '1'], validation_feedback=False)))
    with pytest.raises(ValueError, match='successful campaign exit'):
        freeze(tmp_path, campaign)
    (campaign / 'exit.json').write_text(json.dumps({'exit_code': 0}))
    manifest = freeze(tmp_path, campaign)
    assert manifest['provenance']['source_sha256'] == 'a' * 64
    assert manifest['provenance']['source_tree_verified'] is False
    assert 'active.json' in manifest['sources']
    assert read_frozen_candidate_evidence(tmp_path / 'frozen')['provenance'] == manifest['provenance']


def test_frozen_reader_checks_raw_files_and_count_bound(tmp_path):
    campaign = fixture(tmp_path)
    freeze(tmp_path, campaign)
    with pytest.raises(ValueError, match='count'):
        read_frozen_candidate_evidence(tmp_path / 'frozen', max_files=1)
    (tmp_path / 'frozen/training.json').write_text('{}')
    with pytest.raises(ValueError, match='file changed'):
        read_frozen_candidate_evidence(tmp_path / 'frozen')


def test_source_tree_hash_is_normalized_and_never_executes(tmp_path):
    from hashlib import sha256
    from src.trading_runtime.optimized_candidate_evidence import verify_v19_source_tree
    digest = sha256()
    for version in ('v6', 'v7', 'v19'):
        relative = f'{version}/example.py'
        path = tmp_path / 'research/vectorized_backtest' / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b'raise RuntimeError("must not execute")\r\n')
        digest.update(relative.encode())
        digest.update(b'raise RuntimeError("must not execute")\n')
    receipt = verify_v19_source_tree(tmp_path, digest.hexdigest())
    assert receipt['verified'] is True and receipt['files'] == 3
    with pytest.raises(ValueError, match='differs'):
        verify_v19_source_tree(tmp_path, 'a' * 64)
