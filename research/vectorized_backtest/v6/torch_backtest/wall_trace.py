"""Host wall intervals at existing barriers; never instrument the causal tick."""
from contextlib import contextmanager
from functools import wraps
from threading import Lock, local, get_ident
from time import perf_counter


def union_seconds(intervals):
    end = None
    total = 0.
    for left, right in sorted(intervals):
        if right < left:
            raise ValueError('Reversed wall interval')
        total += max(0., right - max(left, end if end is not None else left))
        end = max(right, end if end is not None else right)
    return total


class WallTrace:
    def __init__(self):
        self.origin = perf_counter()
        self.events = []
        self.lock = Lock()
        self.local = local()

    @contextmanager
    def span(self, name):
        start = perf_counter() - self.origin
        stack = getattr(self.local, 'stack', [])
        self.local.stack = stack
        with self.lock:
            index = len(self.events)
            event = dict(name=name, start=start, end=None, parent=stack[-1] if stack else None,
                         thread=get_ident(), failed=False)
            self.events.append(event)
        stack.append(index)
        try:
            yield
        except BaseException:
            event['failed'] = True
            raise
        finally:
            event['end'] = perf_counter() - self.origin
            stack.pop()

    @contextmanager
    def patches(self, targets):
        originals = []
        try:
            for owner, attribute, name in targets:
                original = getattr(owner, attribute)
                @wraps(original)
                def wrapper(*args, __original=original, __name=name, **kwargs):
                    with self.span(__name):
                        return __original(*args, **kwargs)
                originals.append((owner, attribute, original))
                setattr(owner, attribute, wrapper)
            yield
        finally:
            for owner, attribute, original in reversed(originals):
                setattr(owner, attribute, original)

    def summary(self):
        groups = {}
        for index, event in enumerate(self.events):
            if event['end'] is None:
                raise ValueError('Incomplete wall span')
            row = groups.setdefault(event['name'], dict(calls=0, inclusive_work_seconds=0., exclusive_work_seconds=0., intervals=[]))
            duration = event['end'] - event['start']
            children = [(child['start'], child['end']) for child in self.events if child['parent'] == index]
            row['calls'] += 1
            row['inclusive_work_seconds'] += duration
            row['exclusive_work_seconds'] += duration - union_seconds(children)
            row['intervals'].append((event['start'], event['end']))
        for row in groups.values():
            row['wall_union_seconds'] = union_seconds(row.pop('intervals'))
        return groups
