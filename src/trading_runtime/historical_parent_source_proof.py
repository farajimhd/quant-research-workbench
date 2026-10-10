"""Code-owned historical source approval; never an executing-source certificate."""
from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class HistoricalParentSourceProof:
    release_digest: str
    approved_code_commit: str
    approved_backend_fingerprint: str
    projection_digest: str

    def verify(self, release):
        from .strategy_registry import NumberedStrategyRelease
        if type(release) is not NumberedStrategyRelease:
            raise ValueError('Historical parent requires exact numbered release')
        release.verify()
        fields = ((self.release_digest, 64), (self.approved_code_commit, 40),
            (self.approved_backend_fingerprint, 64), (self.projection_digest, 64))
        if any(type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value)
               for value, size in fields) or self.release_digest != release.approved_digest:
            raise ValueError('Historical parent source approval differs from sealed release')
        return self.projection_digest
