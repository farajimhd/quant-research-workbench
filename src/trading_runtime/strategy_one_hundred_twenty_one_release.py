"""Immutable packet-projection correction; no trading-policy change."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_release as parent
CONTROL_REVISION = 'strategy-one-120:8ccf615d-2706-49be-8343-8a3ed35f796f'
CONTROL_PAYLOAD_HASH = '0683dcefc2a6912fa1ceb43b95fdee26b85f010cdcfec6dda69ec2cdd4926083'
BEHAVIOR = 'Retain every Strategy120 trading policy, including entries, sizing, cash, costs, protection and exit priority. Retain two wholly post-acquisition declining completed one-minute bars, latest close below the first post-acquisition close and a fresh bid below that reference. Discover bounded complete native source packets and verify each exact projection against the certified feature parent. Reject overlapping attempts, missing coverage and mixed horizons; retain full installed row certification and producer fences. Record excluded foreign projection identities. Preserve sequential Portfolio/OMS admission, fills, cash release and OCA ordering. PM/AH only; AH uses prior-day V7 and full regular-session warmup. Backtest-only; financial benefit, full-session performance and validation remain unproven.'
def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=121, executor_revision=121, behavior_specification=BEHAVIOR, approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release
def declared_policies():
    return deepcopy(parent.declared_policies())
