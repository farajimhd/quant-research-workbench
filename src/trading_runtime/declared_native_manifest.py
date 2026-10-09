"""Code-owned immutable manifest metadata; catalog entries do not publish runs."""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class NativeManifestAuthority:
    parent_number: int
    source_prefix: str
    derive: Callable
    verify_manifest: Callable
    certify_source: Callable
    parent_release_factory: Callable

    def verify(self):
        if (type(self.parent_number) is not int or self.parent_number < 1
                or type(self.source_prefix) is not str or not self.source_prefix
                or ':' in self.source_prefix or self.source_prefix.strip() != self.source_prefix
                or any(not callable(f) for f in
                    (self.derive, self.verify_manifest, self.certify_source, self.parent_release_factory))):
            raise ValueError('Native manifest authority lacks exact code-owned metadata')
        parent = self.parent_release_factory()
        parent.verify()
        if parent.number != self.parent_number:
            raise ValueError('Native manifest parent differs from exact release factory')


def registered_manifest_authority(number):
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    try:
        release = numbered_strategy(number)
    except ValueError as exc:
        if str(exc) == f'Strategy {number} is not published':
            return None
        raise
    registration = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    registration.verify()
    authority = registration.manifest_authority
    if authority is None:
        return None
    if type(authority) is not NativeManifestAuthority:
        raise ValueError('Native manifest metadata has an unsupported type')
    authority.verify()
    if authority.parent_number == release.number:
        raise ValueError('Native manifest parent cannot refer to itself')
    if numbered_strategy(authority.parent_number) != authority.parent_release_factory():
        raise ValueError('Native manifest installed parent differs from exact release factory')
    return authority
