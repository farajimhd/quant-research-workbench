import threading
import pytest

from research.vectorized_backtest.v4.torch_backtest.reference_prefetch import ordered_references


def test_ordered_private_readers_and_bounded_input():
    closed = []
    issued = []
    owner = {}

    class Reader:
        def __init__(self):
            owner[id(self)] = threading.get_ident()
        def close(self):
            closed.append(self)

    def listings():
        for index in range(30):
            issued.append(index)
            yield {'ticker': str(index)}

    def read(reader, session, listing):
        assert owner[id(reader)] == threading.get_ident()
        return session, listing['ticker']

    stream = ordered_references(listings(), 'day', read_reference=read,
                                reader_factory=Reader, workers=4)
    first = next(stream)
    assert first == ({'ticker': '0'}, ('day', '0'))
    assert len(issued) == 9
    results = [first, *stream]
    assert [row['ticker'] for row, _ in results] == [str(i) for i in range(30)]
    assert len(closed) == 30


def test_reference_failure_is_not_masked_and_readers_close():
    closed = []
    class Reader:
        def close(self):
            closed.append(self)
    def read(reader, session, listing):
        raise ValueError('uncertified reference')
    with pytest.raises(ValueError, match='uncertified reference'):
        list(ordered_references([{'ticker': 'A'}], 'day', read_reference=read,
                                reader_factory=Reader))
    assert len(closed) == 1
