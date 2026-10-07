"""Bounded ordered reference reads with a private reader per worker."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from contextlib import closing


def ordered_references(listings, session, *, read_reference, reader_factory, workers=4):
    """Preserve input order and failures; never share HTTP readers across threads."""
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('Reference readers must be 1..8')

    def read(listing):
        with closing(reader_factory()) as reader:
            return listing, read_reference(reader, session, listing)

    items = iter(listings)
    pending = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            for _ in range(2 * workers):
                item = next(items, None)
                if item is None:
                    break
                pending.append(pool.submit(read, item))
            while pending:
                result = pending.popleft().result()
                item = next(items, None)
                if item is not None:
                    pending.append(pool.submit(read, item))
                yield result
        finally:
            for future in pending:
                future.cancel()
