"""Exact reviewed technical repairs; never a wildcard for immutable releases.

Outside the backend fingerprint domain so a fingerprint can bind its full
executor bytes without a self-referential hash. Strategy parameters, release
rows and input seals remain immutable. New source requires separate review.
"""

APPROVED_REPAIRS = frozenset({
    # 41222b767 release executor -> bounded native callback-suffix fence repair.
    ("a78ffa266194181d2139744edf33aa57c07d9962a62d73c948e07f659e47c041",
     "eae66f989897873fa746e7524a739db5c6d5a6b3a0c7de6ff6a873cd2fc0b1ff"),
})


def compatible_executor(*, approved, current):
    return approved == current or (approved, current) in APPROVED_REPAIRS
