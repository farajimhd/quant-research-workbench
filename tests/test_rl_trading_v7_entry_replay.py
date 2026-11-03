from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest

from research.rl_trading.v7.config import Config
from research.rl_trading.v7.experiments.entry_replay import replay_session
from research.rl_trading.v7.experiments import entry_replay as replay, entry_score
from research.rl_trading.v7.experiments.predictive_data import sampled_seconds


def session():
    n = 2400
    execution = {k: np.full((n, 1), value, dtype=dtype) for k, value, dtype in (
        ('mark', 10., float), ('bid', 9.99, float), ('ask', 10.01, float),
        ('bid_size', 1000., float), ('ask_size', 1000., float),
        ('quote_valid', True, bool), ('quote_us', 0, np.int64))}
    execution['quote_us'][:, 0] = np.arange(n)*1000000
    selection = np.full((1200, 5), -1, np.int32)
    selection[:, 0] = 0
    return SimpleNamespace(day='2026-01-05', tickers=['A'], clocks=np.arange(n),
        selection=selection, features=np.zeros((1200, 5, 98), np.float32), execution=execution)


class Scorer:
    row_step = 10
    binding = {'synthetic_frozen_score': True}

    def __init__(self, value):
        self.value = value
        self.observations = []

    def score(self, observations):
        self.observations.extend(observations)
        # An unavailable candidate has a higher score; action mask must exclude it.
        return np.tile([self.value, 1000., 1000., 1000., 1000.], (len(observations), 1))


def test_frozen_entry_grid_masks_next_second_fills_and_account_cash():
    s = session()
    scorer = Scorer(1.)
    result = replay_session(s, scorer, replace(Config(), max_hold_seconds=2))
    scheduled = sampled_seconds(s.day)[::10]
    scheduled = scheduled[scheduled < len(s.selection)-1]
    buys = [f for f in result['fill_records'] if f['side'] == 1]
    assert [f['second'] for f in buys] == (scheduled+1).tolist()
    assert result['entries'] == result['exits'] == len(scheduled)
    assert result['valid_terminal'] and result['return_ratio'] < 0
    assert all(not o['holding'][0] for o in scorer.observations)
    assert scorer.observations[-1]['account'][0] < 1.
    assert all(d['action'] == 1 for d in result['decisions'])


@pytest.mark.parametrize('score', [0., -1.])
def test_nonpositive_score_never_buys(score):
    result = replay_session(session(), Scorer(score))
    assert result['valid_terminal'] and result['entries'] == 0
    assert result['return_ratio'] == 0.


def test_nonfinite_eligible_score_fails_closed():
    with pytest.raises(ValueError, match='eligible entry score'):
        replay_session(session(), Scorer(float('nan')))


def test_actual_main_sealed_resume_and_hash_guard(monkeypatch, tmp_path):
    opened = []
    sample = session()

    def load(day):
        opened.append(day)
        return sample

    data = SimpleNamespace(days=[sample.day], binding={'synthetic_training': True}, load=load)
    monkeypatch.setattr(replay, 'TrainingDataset', lambda _: data)
    monkeypatch.setattr(entry_score, 'load_bundle', lambda *a, **k: Scorer(-1.))
    output = tmp_path/'replay'
    args = ['--bundle', 'unused', '--forecast', 'unused', '--output', str(output)]
    assert replay.main(args) == 0
    assert replay.main(args) == 0
    assert opened == [sample.day]
    path = output/(sample.day+'.json')
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='integrity'):
        replay.main(args)


def test_validation_rejected_before_loading_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, 'TrainingDataset', lambda _: SimpleNamespace(days=['2026-01-05']))
    monkeypatch.setattr(entry_score, 'load_bundle', lambda *a, **k: pytest.fail('Unexpected model access'))
    with pytest.raises(ValueError, match='TRAINING'):
        replay.main(['--bundle', 'unused', '--forecast', 'unused', '--output', str(tmp_path/'invalid'),
                     '--dates', '2026-08-11'])
