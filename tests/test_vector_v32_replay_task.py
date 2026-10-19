from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v32 import stream_sessions as module


def test_replay_events_preserve_exact_graph_counts_and_wait_for_all_streams(monkeypatch):
    class Stream:
        def wait_stream(self, other): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Event:
        def __init__(self): self.ready = False
        def record(self, stream): self.ready = False
        def query(self): return self.ready
        def synchronize(self): self.ready = True
    monkeypatch.setattr(module, 'torch', SimpleNamespace(cuda=SimpleNamespace(Stream=Stream, Event=Event,
        current_stream=lambda: None, stream=lambda stream: stream)))
    counts = [0, 0]; resets = []
    def graph(index, total):
        def replay(): counts[index] += 1
        return SimpleNamespace(total=total, block=2, reset=lambda: resets.append(index),
                               graph=SimpleNamespace(replay=replay))
    task = module.ReplayTask([graph(0, 5), graph(1, 9)])
    assert resets == [0, 1]
    task.enqueue(2)
    assert counts == [2, 2] and not task.poll()
    with pytest.raises(RuntimeError): task.enqueue(2)
    task.events[0].ready = True
    assert not task.poll() and task.completed == 0
    task.events[1].ready = True
    assert task.poll() and task.completed == 2
    task.enqueue(2); task.drain()
    assert counts == [3, 4] and not task.done
    task.enqueue(2); task.drain()
    assert counts == [3, 5] and task.done
    with pytest.raises(RuntimeError): task.enqueue(2)

