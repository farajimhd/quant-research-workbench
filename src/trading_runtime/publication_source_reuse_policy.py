"""Explicit single-publication source replay retention; no admission granted."""
from dataclasses import dataclass

INPUT = 'declared-single-publication-source-reuse-bounds@1'
RULE = 'single-publication-complete-source-replay-reuse@1'
PARAMETER = 'publication_source_reuse_policy'


@dataclass(frozen=True, slots=True)
class PublicationSourceReusePolicy:
    max_image_bytes: int

    def __post_init__(self):
        if type(self.max_image_bytes) is not int or not 1 <= self.max_image_bytes <= 67108864:
            raise ValueError('Explicit bounded publication source reuse required')

    def payload(self):
        self.__post_init__()
        return {'max_image_bytes': self.max_image_bytes}


def require_declared_publication_source_reuse(release, policy):
    release.verify()
    claimed = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    if not claimed:
        if policy is not None:
            raise ValueError('Undeclared publication source reuse policy')
        return None
    if (release.input_contracts.count(INPUT) != 1
            or release.rule_set_contracts.count(RULE) != 1
            or type(policy) is not PublicationSourceReusePolicy):
        raise ValueError('Exact paired publication source reuse declaration required')
    policy.__post_init__()
    return policy


def parse_declared_publication_source_reuse(release, value):
    if value is None:
        return require_declared_publication_source_reuse(release, None)
    if type(value) is not dict or set(value) != {'max_image_bytes'}:
        raise ValueError('Canonical publication source reuse payload required')
    return require_declared_publication_source_reuse(release, PublicationSourceReusePolicy(**value))
