"""Addressable slot construction; failed proposals never become replay slots."""
from collections import Counter
import numpy as np
from .authority import dependency_certificate


def canonical_key(member):
    # Use the same immutable genotype identity as reproduction/packing rather
    # than serialize and hash the entire population a second time.
    from .lineage import program_key
    return program_key(member)


def build(size, producer, fallback, *, seed, generation=0, initial=(), attempts=8):
    """producer(rng, slot, attempt); deterministic despite task completion order.

    At most `attempts` operator attempts and `attempts` fresh attempts per slot.
    Exhaustion fails explicitly rather than submitting an incomplete population.
    """
    if size <= 0 or attempts <= 0 or len(initial) > size:
        raise ValueError('Invalid population build dimensions')
    accepted = list(initial)
    seen = set()
    report = Counter()
    for member in accepted:
        member.validate(); dependency_certificate(member)
        key = canonical_key(member)
        if key in seen:
            raise ValueError('Duplicate restored population')
        seen.add(key)
    for slot in range(len(accepted), size):
        for attempt in range(2 * attempts):
            rng = np.random.default_rng(np.random.SeedSequence([seed, generation, slot, attempt]))
            fresh = attempt >= attempts
            try:
                member = (fallback if fresh else producer)(rng, slot, attempt)
                member.validate(); dependency_certificate(member)
                key = canonical_key(member)
                if key in seen:
                    report['duplicate'] += 1
                    continue
            except ValueError as exc:
                report['rejected:' + str(exc)] += 1
                continue
            seen.add(key); accepted.append(member)
            report['fallback_fresh' if fresh else 'intended_operator'] += 1
            break
        else:
            raise RuntimeError(f'Population construction exhausted at slot {slot}/{size}: {dict(report)}')
    assert len(accepted) == size == len(seen)
    return accepted, dict(report, accepted=size)
