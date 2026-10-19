import copy
import json
from pathlib import Path
import numpy as np
import pytest

from research.vectorized_backtest.v32.genome import Individual, Network, sample
from research.vectorized_backtest.v32 import publication


def test_compact_publication_preserves_numbers_and_rejects_nonfinite(tmp_path):
    p = tmp_path / 'state.json'
    value = {'rng': 2**127 + 123, 'x': [-0., 1e-300, 1.2345678901234567], 'label': 'é'}
    publication.write_json(p, value)
    assert p.read_text() == json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    old = p.read_bytes()
    with pytest.raises(ValueError):
        publication.write_json(p, {'bad': float('nan')})
    assert p.read_bytes() == old and not list(tmp_path.glob('*.tmp'))


def test_failed_replace_preserves_old_file(tmp_path, monkeypatch):
    p = tmp_path / 'state.json'
    publication.write_json(p, {'old': True})
    def fail(*args): raise OSError('disk failure')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='disk failure'):
        publication.write_json(p, {'new': True})
    assert json.loads(p.read_bytes()) == {'old': True}
    assert not list(tmp_path.glob('*.tmp'))


def test_checkpoint_bytes_equal_canonical_reference(tmp_path):
    from research.vectorized_backtest.v32.run_search import digest
    state = {'rng': 2**127 + 1, 'weights': [1e-100, -0., 1.23456789012345], 'labels': ['é', '"']}
    contract = {'source': 'fixture', 'nested': {'a': True}}
    p = tmp_path / 'checkpoint.json'
    publication.write_checkpoint(p, contract, state)
    expected = dict(contract=contract, state=state, state_sha256=digest(state))
    assert p.read_bytes() == json.dumps(expected, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def test_restore_validates_once_and_rejects_corruption(monkeypatch):
    member = sample(np.random.default_rng(2020), 1)[0]
    calls = []
    original = Network.validate
    def validate(net):
        calls.append(net)
        return original(net)
    monkeypatch.setattr(Network, 'validate', validate)
    assert Individual.restore(member.payload()) == member
    assert len(calls) == 5
    for field, bad in [('threshold', 1.), ('size_source', 10000)]:
        payload = copy.deepcopy(member.payload())
        payload['networks']['entry'][field] = bad
        with pytest.raises(ValueError): Individual.restore(payload)
    payload = copy.deepcopy(member.payload())
    payload['networks']['exit']['nodes'][0]['weights'] = (float('nan'),)
    with pytest.raises(ValueError): Individual.restore(payload)


def test_spawned_evolution_exact_population_rng_pedigree_species(monkeypatch):
    from research.vectorized_backtest.v32.lineage import Selection, next_population
    from research.vectorized_backtest.v32.local_runner import CompactPedigree
    from research.vectorized_backtest.v32.species import Species
    from research.vectorized_backtest.v32.evolution_work import next_population as spawned
    from research.vectorized_backtest.v32.preparation import close
    from test_vector_v32 import metrics_fixture
    monkeypatch.setenv('V32_CPU_RESERVE_GIB', '0')
    monkeypatch.setenv('V32_CPU_ADMISSION_GIB', '0')
    monkeypatch.setenv('V32_REPRODUCTION_WORKERS', '1')
    members = sample(np.random.default_rng(2020), 20)
    ped = CompactPedigree(3)
    ids = ped.birth(members, 1, [(-1, -1)] * 20, ['fresh'] * 20)
    p2 = copy.deepcopy(ped); a = Species(); b = Species()
    ra = np.random.default_rng(71); rb = np.random.default_rng(71)
    values = np.arange(1, 21, dtype=float); eligible = np.ones(20, dtype=bool)
    kwargs = dict(session_values=values[None], session_eligible=eligible[None], global_values=values,
                  global_eligible=eligible, target_size=20, ordered=[metrics_fixture(20)])
    x, ix, rx = next_population(ra, members, ids, ped, list(range(19, -1, -1)), 2, Selection(), species=a, **kwargs)
    try:
        y, iy, ry = spawned(rb, members, ids, p2, list(range(19, -1, -1)), 2, Selection(), species=b, **kwargs)
    finally:
        close()
    assert x == y and np.array_equal(ix, iy)
    assert ra.bit_generator.state == rb.bit_generator.state
    assert ped.__dict__ == p2.__dict__ and a.state == b.state
    for key in ('species_seconds', 'telemetry_seconds'):
        rx.pop(key); ry.pop(key)
    assert rx == ry


def test_resident_metadata_does_not_queue_behind_gpu_and_is_bounded(tmp_path):
    from types import SimpleNamespace
    from research.vectorized_backtest.v32.stream_sessions import Proxy
    class Service:
        def __init__(self): self.calls = []
        def call(self, key, method, *args):
            self.calls.append((method, args))
            if method == 'load':
                return SimpleNamespace(identity={'day': args[0]}, listing_ids=np.array([1, 2]))
    service = Service()
    p = Proxy(service, dict(output=str(tmp_path), retain_session_residency=True), None, 0)
    first = p.load('a')
    assert p.load('a') is first
    assert [x[0] for x in service.calls] == ['create', 'load']
    assert p.load('b').identity == {'day': 'b'}
    assert p.loaded_metadata[0] == 'b'
    p.close()
    assert p.loaded_metadata is None
