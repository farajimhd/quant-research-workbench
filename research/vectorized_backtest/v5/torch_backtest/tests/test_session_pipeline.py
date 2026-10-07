"""Overlap witnesses, all-session selection, bounded failures and GPU parity."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.vectorized_backtest.v5.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v5.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v5.torch_backtest.optimize import phase
from research.vectorized_backtest.v5.torch_backtest.session_pipeline import PreparedSessions
from research.vectorized_backtest.v5.torch_backtest.session_pool import SessionPool


def test_preparation_overlaps_first_replay_and_selection_waits_all(tmp_path):
    started, release = Event(), Event()
    rows_seen, selected = [], []

    def loader(index):
        if index == 1:
            started.set()
            assert release.wait(10)
        return synthetic_tape(seconds=20)

    args = SimpleNamespace(seed=17, population=4, generations=1,
                           minimum_training_entries=1, weights={})
    with PreparedSessions(3, loader, workers=2, lookahead=2) as supplier:
        class Objective:
            def __init__(self, index):
                self.index = index

            def prepare(self):
                supplier.get(self.index)

            def __call__(self, rows):
                if self.index == 0:
                    assert started.wait(10)
                    assert supplier.peek(1) is None  # GPU need not wait for day 2.
                    assert not selected
                    release.set()
                rows_seen.append(rows.copy())
                b = len(rows)
                return dict(net_pnl=[float(self.index)] * b, drawdown=[0.] * b,
                            positions_opened=[1] * b, filled_batches=[1] * b,
                            exposure_seconds=[0.] * b, terminal_valid=[True] * b,
                            stop_risk_dollar_seconds=[0.] * b, capital_dollar_seconds=[0.] * b,
                            compile_seconds=0., replay_seconds=0.)

        def seal():
            assert len(rows_seen) == 3
            assert supplier.state()['consumed'] == 3
            selected.append(True)

        phase([Objective(i) for i in range(3)], StrategySpace(), args, tmp_path,
              before_selection=seal)
        assert len(selected) == 1
        assert all(np.array_equal(rows_seen[0], rows) for rows in rows_seen)


def test_failure_and_memory_envelope_do_not_skip_sessions():
    def fail(index):
        raise ValueError('missing certified input')

    with PreparedSessions(2, fail) as supplier:
        with pytest.raises(ValueError, match='certified'):
            supplier.get(0)
        assert supplier.state()['failed'] >= 1
    with PreparedSessions(1, lambda i: synthetic_tape(),
                          maximum_host_gib=1, maximum_tape_gib=1) as supplier:
        with pytest.raises(MemoryError, match='host envelope'):
            supplier.get(0)


@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_streamed_sessions_match_static_pool_and_async_transfer(device, tmp_path):
    if device == 'cuda':
        if not torch.cuda.is_available():
            pytest.skip('CUDA unavailable')
        from research.vectorized_backtest.v5.torch_backtest.runtime import configure_caches
        configure_caches(tmp_path)
    torch.set_num_threads(1)
    tapes = [synthetic_tape(seconds=40, listings=2) for _ in range(3)]
    space = StrategySpace()
    rows = space.sample(np.random.default_rng(33), 4)
    rows[0] = space.default
    backend = 'compiled_graph' if device == 'cuda' else 'eager'
    static = SessionPool(tapes, space, 4, device=device, backend=backend,
                         resident_gib=0, graph_steps=8, prefetch=False)
    expected = [static.evaluate(i, rows) for i in range(3)]
    static.close()
    with PreparedSessions(3, lambda i: tapes[i]) as supplier:
        # Finish producers before capture so a next tape is certainly available.
        supplier.get(0)
        supplier.get(1)
        pool = SessionPool([], space, 4, supplier=supplier, device=device,
                           backend=backend, resident_gib=0, graph_steps=8)
        try:
            actual = [pool.evaluate(i, rows) for i in range(3)]
            for a, b in zip(actual, expected):
                for key in ('net_pnl', 'drawdown', 'fill_count', 'positions_opened',
                            'open_positions', 'filled_entry_shares', 'entry_retry_count'):
                    np.testing.assert_allclose(a[key], b[key], rtol=0, atol=1e-7)
            if device == 'cuda':
                assert pool.prefetched_sessions == 2
                assert supplier.state()['pinned_staging_gib'] == 0
        finally:
            pool.close()


def test_online_capacity_growth_preserves_account_reset_and_source():
    torch.set_num_threads(1)
    tapes = [synthetic_tape(seconds=20, listings=n) for n in (1, 66)]
    rows = [StrategySpace().default]
    with PreparedSessions(2, lambda i: tapes[i]) as supplier:
        pool = SessionPool([], StrategySpace(), 1, supplier=supplier, device='cpu', backend='eager')
        try:
            first = pool.evaluate(0, rows)
            result = pool.evaluate(1, rows)
            direct = SessionPool([tapes[1]], StrategySpace(), 1, device='cpu', backend='eager')
            assert result['net_pnl'] == direct.evaluate(0, rows)['net_pnl']
            assert pool.capacity == 128 and pool.capacity_growths == 1
            repeated = pool.evaluate(0, rows)
            for key in ('net_pnl', 'drawdown', 'fill_count', 'filled_entry_shares'):
                assert first[key] == repeated[key]
            assert next(iter(pool.evaluators.values())).tape.tickers[:1] == tapes[0].tickers
        finally:
            pool.close()


@pytest.mark.parametrize('device', ['cpu', 'cuda'])
def test_streamed_v7_book_width_does_not_recompile_or_change_geometry(device, tmp_path):
    if device == 'cuda':
        if not torch.cuda.is_available():
            pytest.skip('CUDA unavailable')
        from research.vectorized_backtest.v5.torch_backtest.runtime import configure_caches
        configure_caches(tmp_path)
    from research.vectorized_backtest.v5.torch_backtest.search_runner import SearchRunner
    torch.set_num_threads(1)
    a, b = [synthetic_tape(seconds=40, listings=2) for _ in range(2)]
    for tape in (a, b):
        tape.structural_targets = tape.level_lower[None].expand(40, -1, -1).clone()
    for name in ('level_from', 'level_to', 'level_lower', 'level_resistance'):
        value = getattr(b, name)
        setattr(b, name, torch.cat((value, value), 1))
    b.validate()
    space = StrategySpace()
    rows = [space.default] * 2
    backend = 'compiled_graph' if device == 'cuda' else 'eager'
    pool = SessionPool([a, b], space, 2, device=device, backend=backend, graph_steps=8)
    try:
        pool.evaluate(0, rows)
        pointer = next(iter(pool.evaluators.values())).tape.close.data_ptr()
        result = pool.evaluate(1, rows)
        assert result['compile_seconds'] == 0 and len(pool.evaluators) == 1
        assert next(iter(pool.evaluators.values())).tape.close.data_ptr() == pointer
        assert next(iter(pool.evaluators.values())).tape.level_lower.shape[1] == 1
        reference = SearchRunner(b, space, rows).run()
        np.testing.assert_allclose(result['net_pnl'], reference['net_pnl'].tolist(), rtol=0, atol=1e-7)
    finally:
        pool.close()


def test_import_preserves_creator_identity_and_rejects_incompatible_inputs(tmp_path):
    from dataclasses import asdict
    from hashlib import sha256
    from pathlib import Path
    from research.vectorized_backtest.v5.torch_backtest import prepare
    from research.vectorized_backtest.v5.torch_backtest.prepared_cache import import_prepared, save_prepared
    from research.vectorized_backtest.v5.torch_backtest.runtime import write_json

    space = StrategySpace()
    tape = synthetic_tape(seconds=20)
    tape.provenance.update(source_build='producer', preparation_algorithm=sha256(Path(prepare.__file__).read_bytes()).hexdigest())
    origin = tmp_path / 'old' / 'inputs' / 'training_000'
    creator = dict(code_hash='old-code', build_id='producer', session={'day': '2026-07-30'}, manifest_sha256='manifest')
    write_json(tmp_path / 'old' / 'identity.json', dict(code_hash='old-code', grammar=space.manifest()))
    save_prepared(origin, tape, creator)
    original = (origin / 'receipt.json').read_bytes()
    current = dict(creator, code_hash='new-code')
    result = import_prepared(origin, tmp_path / 'new' / 'inputs' / 'training_000', current, space.manifest())
    assert result.provenance == tape.provenance
    assert (origin / 'receipt.json').read_bytes() == original
    with pytest.raises(ValueError, match='producer/session'):
        import_prepared(origin, tmp_path / 'invalid', dict(current, build_id='other'), space.manifest())


@pytest.mark.parametrize('width,height', [(120, 28), (80, 24), (60, 15)])
def test_pipeline_panel_keeps_activity_wait_and_failure_visible(width, height):
    from io import StringIO
    from rich.console import Console
    from research.vectorized_backtest.v5.torch_backtest.optimization_ui import render_search

    output = StringIO()
    console = Console(file=output, width=width, height=height, force_terminal=False)
    state = dict(status='training', stage='Replay population', pipeline=dict(
        ready=2, total=30, active=2, queued=1, failed=0, consumed=1,
        host_gib=8., reserved_gib=48., data_wait_seconds=10., waiting_session=2))
    console.print(render_search(state, 40, width=width, height=height))
    rendered = output.getvalue()
    assert 'PREP ready 2/30' in rendered and 'GPU WAITING' in rendered
    assert len(rendered.splitlines()) <= height
