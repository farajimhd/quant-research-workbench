"""Canonical identities; no mutable dictionary identity cache."""
from contextlib import nullcontext
from .identity import digest
identity_scope=nullcontext
def program_key(member):
    p=member.payload()
    for n in p['networks']['shared']['nodes']:n.pop('uid',None)
    return digest(p)
