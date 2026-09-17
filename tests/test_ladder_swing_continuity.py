import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.backend.replay_run_service import ReplayFrameSpool
from src.trading_runtime.structural_recovery import MarketStream
from tests.test_vwap_resistance_ladder import fixture, advance


START = datetime(2026, 8, 21, 8, 9, 44, tzinfo=timezone.utc).timestamp()
BOOK = dict(id='fixture', fingerprint='pinned', version='causal-level-book-v7-mle-1')


def bar(end, price):
    return dict(time=end-1, end=end, open=price, high=price, low=price, close=price, volume=100)


def prepared(tmp_path):
    spool = ReplayFrameSpool(tmp_path / 'frames.sqlite3')
    bars = [bar(START+i, p) for i, p in enumerate([3.5, 3.42, 3.48, 3.37, 3.36, 3.42, 3.44])]
    bars.append(bar(START+9, 3.5))
    with sqlite3.connect(spool.path) as db:
        for i, b in enumerate(bars):
            db.execute('INSERT INTO strategy_frames VALUES (?,?,?,?,?,?,?)',
                (round(b['end']*1e6), datetime.fromtimestamp(b['end'], timezone.utc).isoformat(),
                 'SUGP', '1s', i, json.dumps(b), '{}'))
        db.execute('INSERT INTO strategy_frame_streams VALUES (?,?,?,?)', ('SUGP', '1s', 'complete',
            json.dumps(dict(complete_for_history=True, revision_token='source', source_plan_hash='plan'))))
    return spool, bars


def test_confirmed_low_survives_verified_gap_and_checkpoint(tmp_path):
    spool, bars = prepared(tmp_path)
    stream = MarketStream()
    for b in bars[:-1]:
        stream.observe(b, [], BOOK)
    assert any(s['price'] == 3.36 for s in stream.saved['row']['local_swings'])
    recovered = MarketStream(json.loads(json.dumps(stream.checkpoint())))
    proof = spool.empty_interval('SUGP', bars[-2]['end'], bars[-1]['end'])
    result = stream.observe(bars[-1], [], BOOK, continuity=proof)
    assert result == recovered.observe(bars[-1], [], BOOK, continuity=proof)
    assert not result['reset']
    assert result['row']['continuity']['status'] == 'certified_empty_interval'
    assert any(s['price'] == 3.36 for s in result['row']['local_swings'])


@pytest.mark.parametrize('bad', ['missing_authority', 'incomplete', 'omitted_frame', 'missing_border'])
def test_gap_proof_rejects_uncertain_or_skipped_data(tmp_path, bad):
    spool, bars = prepared(tmp_path)
    with sqlite3.connect(spool.path) as db:
        if bad == 'missing_authority':
            db.execute('DELETE FROM strategy_frame_streams')
        elif bad == 'incomplete':
            db.execute("UPDATE strategy_frame_streams SET authority_json='{}'")
        elif bad == 'omitted_frame':
            db.execute("INSERT INTO strategy_frames VALUES (?, '', 'SUGP', '1s', 20, '{}', '{}')",
                       (round((START+7)*1e6),))
        else:
            db.execute('DELETE FROM strategy_frames WHERE as_of_us=?', (round(bars[-1]['end']*1e6),))
    proof = spool.empty_interval('SUGP', bars[-2]['end'], bars[-1]['end'])
    assert proof is None
    stream = MarketStream()
    for b in bars[:-1]:
        stream.observe(b, [], BOOK)
    result = stream.observe(bars[-1], [], BOOK, continuity=proof)
    assert result['reset'] and not result['row']['local_swings']


def test_old_swing_remains_eligible_when_a_new_macd_episode_starts():
    host, assignment, observation = fixture()
    first = host.evaluate(assignment, observation(bearish=True))
    assert not first.evaluation.intents
    result = host.evaluate(advance(assignment, first), observation(i=30))
    assert result.evaluation.intents[0].action == 'enter_long'
    assert result.state['vwap_ladder_entry']['swing']['pivot_at'] < result.state['vwap_ladder_episode']['started_at']
