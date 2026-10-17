"""Raw source identities from certified V21 df9d81af2-r2 (not normalized)."""
from hashlib import sha256
from pathlib import Path

BASELINE_COMMIT='df9d81af2'
BASELINE_SOURCE_SHA256='4ce1779f3a381f50c18f7af38264ffcdb020640dc97a63250c5e3cd0e49ddda0'

RAW_SHA256={
    'indicator_math.py':'0c351b46c290d193bae13c10af655c2c5e9649e00cbc0a6308c45d9ddeb98bfc',
    'enriched_math.py':'5ba0d558555d4e5e2b099f884f3fc6d504c8ec351efc347175a68a46405c684e',
    'features.py':'1d432518a2e1529baa04ced8513e22f74aff8b517f115fa0c66ee62d39b56ba5',
    'indicator_identity.py':'a449b30056a8b2a56a04ae59010c5695d1669d0120ee2674186a02196f9f16dd',
    'search_space.py':'1594a0d82517d7517d7712e0af64b04692ce59832bda075ddc24c60a47a2b97d',
}


def verify(root=None):
    root=Path(__file__).parent if root is None else Path(root)
    for name,expected in RAW_SHA256.items():
        if sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Certified V21 source bytes changed: '+name)
    return dict(RAW_SHA256)
