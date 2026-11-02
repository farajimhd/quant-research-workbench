"""Fresh explicit price programs; no unused execution-policy genes."""
from .authority import construct

def policies(rng,members):
    return [construct(m) for m in members]
