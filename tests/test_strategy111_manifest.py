"""Whole-tree comparison using controlled certified-parent transport only."""
from copy import deepcopy
from dataclasses import fields

import pytest

from tests.test_strategy108_full_manifest import manifest
from src.trading_runtime.strategy_one_hundred_ten_contract import strategy_one_hundred_ten_contract
from src.trading_runtime.strategy_one_hundred_eleven_contract import strategy_one_hundred_eleven_contract
from src.trading_runtime.strategy_one_hundred_ten_release import derive_strategy_one_hundred_ten_configuration
from src.trading_runtime.strategy_one_hundred_eleven_release import (
    derive_strategy_one_hundred_eleven_configuration,
    verify_prepared_strategy_one_hundred_eleven_configuration,
)
from src.trading_runtime.publication_source_reuse_policy import PARAMETER


def test_all_typed_economic_fields_are_inherited():
    prior, candidate = strategy_one_hundred_ten_contract(), strategy_one_hundred_eleven_contract()
    for field in fields(prior):
        if field.name not in {'strategy_number', 'release', PARAMETER}:
            assert getattr(prior, field.name) == getattr(candidate, field.name), field.name


def test_complete_tree_adds_only_declared_policy_and_identity(manifest):
    parent, approval, _ = manifest
    prior = derive_strategy_one_hundred_ten_configuration(parent, **approval)
    candidate = derive_strategy_one_hundred_eleven_configuration(parent, **approval)
    assert verify_prepared_strategy_one_hundred_eleven_configuration(parent, candidate['payload']) == candidate
    normalized = deepcopy(candidate['payload'])
    assert normalized['strategy']['parameters'].pop(PARAMETER) == {'max_image_bytes': 67108864}
    for field in ('strategy_number', 'revision', 'profile_id', 'profile_revision', 'name', 'numbered_release'):
        normalized['strategy'][field] = deepcopy(prior['payload']['strategy'][field])
    normalized['strategy_profile'] = deepcopy(prior['payload']['strategy_profile'])
    normalized['run_plan'] = deepcopy(prior['payload']['run_plan'])
    assert normalized == prior['payload']


@pytest.mark.parametrize('mutation', ['policy', 'cash', 'inherited_execution'])
def test_complete_rederivation_rejects_changed_economics_or_policy(manifest, mutation):
    parent, approval, _ = manifest
    candidate = derive_strategy_one_hundred_eleven_configuration(parent, **approval)
    payload = deepcopy(candidate['payload'])
    if mutation == 'policy': payload['strategy']['parameters'][PARAMETER]['max_image_bytes'] = 4096
    elif mutation == 'cash': payload['accounts']['currency'] = 'CAD'
    else: payload['strategy']['parameters']['execution']['unspecified_override'] = True
    with pytest.raises(ValueError):
        verify_prepared_strategy_one_hundred_eleven_configuration(parent, payload)
