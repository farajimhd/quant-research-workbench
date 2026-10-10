"""Unpublished candidate changing only a declared initial context bound."""
from dataclasses import replace

from .strategy_one_hundred_eleven_contract import strategy_one_hundred_eleven_contract
from .strategy_one_hundred_twelve_release import release_contract, initial_held_recovery_reuse_policy


def strategy_one_hundred_twelve_contract():
    prior = strategy_one_hundred_eleven_contract()
    release = release_contract()
    return replace(prior, strategy_number=release.number, release=release,
        initial_held_recovery_reuse_policy=initial_held_recovery_reuse_policy(
            prior.initial_held_recovery_reuse_policy))
