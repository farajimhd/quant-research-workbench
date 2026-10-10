"""Actual installed native source admission and prepared publisher classification."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import pytest

from src.trading_runtime.historical_parent_source_proof import HistoricalParentSourceProof
from src.trading_runtime.strategy_one_hundred_thirteen_configuration import (
    historical_parent_source_proof, derive_strategy_one_hundred_thirteen_configuration,
)
from src.trading_runtime.strategy_one_hundred_thirteen_release import release_contract
from src.trading_runtime import strategy_fifty_seven_release as parent
from src.trading_runtime.declared_native_manifest import registered_manifest_authority
from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies, numbered_strategy_parent
from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
from src.backend.backtest_price_risk_certification import certify_price_confirmed_original_risk_source
from test_strategy113_configuration import source_fixture, APPROVAL


def test_installed_candidate_uses_exact_historical_parent_and_independent_full_source():
    initialize_numbered_fixed_strategies()
    authority = registered_manifest_authority(release_contract().number)
    authority.verify()
    assert numbered_strategy_parent(release_contract().number) == parent.release_contract().number
    assert authority.historical_parent_proof == historical_parent_source_proof()
    assert authority.certify_source is certify_price_confirmed_original_risk_source
    assert registered_manifest_authority(112).historical_parent_proof is None
    parent_proof = authority.historical_parent_proof.verify(parent.release_contract())
    source_proof = certify_price_confirmed_original_risk_source()
    expected = sha256(json.dumps((parent_proof, source_proof, release_contract().approved_digest),
        separators=(',', ':')).encode()).hexdigest()
    assert certify_numbered_fixed_v4_projection(release_contract().number) == expected


def test_prepared_candidate_uses_real_native_publisher_classification_without_writing():
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    envelope = derive_strategy_one_hundred_thirteen_configuration(source_fixture(), **APPROVAL)
    assert is_numbered_fixed_configuration(envelope['payload'])
    assert _verified_numbered_envelope(envelope)[0] == envelope['payload']


@pytest.mark.parametrize('field,bad', [('release_digest', 'f'*64),
    ('approved_code_commit', True), ('approved_backend_fingerprint', 'short'),
    ('projection_digest', '')])
def test_historical_approval_rejects_wrong_release_or_malformed_identity(field, bad):
    with pytest.raises(ValueError):
        replace(historical_parent_source_proof(), **{field: bad}).verify(parent.release_contract())


def test_historical_approval_is_frozen_and_cannot_use_foreign_parent():
    from dataclasses import FrozenInstanceError
    from src.trading_runtime.strategy_sixty_four_release import release_contract as foreign
    proof = historical_parent_source_proof()
    assert type(proof) is HistoricalParentSourceProof
    with pytest.raises(FrozenInstanceError):
        proof.projection_digest = 'f'*64
    with pytest.raises(ValueError):
        proof.verify(foreign())
    with pytest.raises(ValueError):
        replace(registered_manifest_authority(release_contract().number),
            historical_parent_proof=object()).verify()


@pytest.mark.parametrize('relative', (
    'src/trading_runtime/declared_native_manifest.py',
    'src/backend/backtest_fixed_v4_certification.py',
    'src/trading_runtime/strategy_registry.py'))
def test_native_shared_delta_restores_complete_parent_and_rejects_foreign_code(relative):
    from src.backend.backtest_price_risk_native_compatibility import (
        RESTORATIONS, restore_native_price_risk_parent_source,
    )
    import ast
    source = (Path(__file__).resolve().parents[1] / relative).read_text(encoding='utf-8')
    restored = restore_native_price_risk_parent_source(source, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == RESTORATIONS[relative]['parent_ast']
    with pytest.raises(ValueError, match='current source differs'):
        restore_native_price_risk_parent_source(source + '\nforeign_cash_rule = True\n', relative)
