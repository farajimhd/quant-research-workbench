"""Learning fences preserve warmup events and the intended update budget."""
from types import SimpleNamespace

from research.rl_trading.v6.training import _event_chunks


def test_rth_fence_preserves_all_events_and_starts_32_learning_chunks():
    events = [SimpleNamespace(close_us=i) for i in range(19799 + 1024)]
    chunks = list(_event_chunks(iter(events), 32, 19799))
    assert [event for chunk in chunks for event in chunk] == events
    assert all(not (chunk[0].close_us < 19799 <= chunk[-1].close_us)
               for chunk in chunks)
    learning = [chunk for chunk in chunks if chunk[0].close_us >= 19799]
    assert len(learning) == 32
    assert all(len(chunk) == 32 for chunk in learning)
    assert len(chunks[618]) == 23


def test_no_fence_preserves_original_batching_and_partial_tail():
    events = [SimpleNamespace(close_us=i) for i in range(67)]
    chunks = list(_event_chunks(iter(events), 32))
    assert chunks == [tuple(events[:32]), tuple(events[32:64]), tuple(events[64:])]


def test_fence_at_first_event_or_between_sparse_clocks():
    events = [SimpleNamespace(close_us=i) for i in (1, 4, 9, 15)]
    assert list(_event_chunks(iter(events), 2, 1)) == [tuple(events[:2]), tuple(events[2:])]
    assert list(_event_chunks(iter(events), 3, 7)) == [tuple(events[:2]), tuple(events[2:])]
