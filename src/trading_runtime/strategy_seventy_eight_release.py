"""Immutable exact42 every-reentry causal prior-held-high alternative."""
from dataclasses import replace
from hashlib import sha256
import re
from . import strategy_forty_two_release as parent_policy
from .strategy_registry import NumberedStrategyRelease
from .journal_contract import canonical_json
from .prior_position_high_reentry import (PriorPositionHighReentryPolicy,
    PRIOR_POSITION_HIGH_REENTRY_RULE, PRIOR_POSITION_HIGH_REENTRY_INPUT)
from .prior_position_high_reentry_release import (ReentryParentBinding,
    derive_reentry_configuration, verify_reentry_parent)

PARENT_REVISION_ID = 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
PARENT_PAYLOAD_HASH = '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
PARENT_NODE_HASH = 'df466e9e5d524c6e4effb4521dec100da57ac0300c50108f344e05992d9d8108'
PARENT_SOURCE_CANDIDATE_ID = 'strategy-forty-two-from:strategy-one-41:6fd9d95a-3442-49cd-bf7d-2637815685dc'
PARENT_SOURCE_CANDIDATE_HASH = '8cf4474174c222b900d39b72f7d5f7dc759ede2e56e753599cd9a1262942211d'
PARENT_CODE_COMMIT = '9ad409381449c1f1b859206093b6851282b111bb'
PARENT_CODE_FINGERPRINT = 'bfa8ad70e0584f27d3159ee1017a78d50ec53e6f7ae2dc601e98429f964eb62e'
INHERITED_POLICIES = parent_policy.INHERITED_POLICIES
HALF_RISK_LIQUIDITY_POLICY = parent_policy.HALF_RISK_LIQUIDITY_POLICY
REENTRY_POLICY = PriorPositionHighReentryPolicy().payload()
BEHAVIOR = 'Strategy78 preserves exact published42 original entries, episode TTL, protection, exits and precedence, sizing, exposure, costs and sequential Portfolio/OMS cash. First acquisition is unchanged. Every subsequent acquisition requires the current completed100ms close strictly above the prior completed held-position high and the previous completed price-bearing100ms close at or below that high. Missing causal prior position or certified prior close rejects reentry. No time-only holding exit or original-risk exit extension. PM/AH source contracts and original anchors remain authoritative. Backtest-only; financial benefit unproven.'


def parent_binding():
    return ReentryParentBinding(parent_policy.release_contract(), PARENT_REVISION_ID,
        PARENT_PAYLOAD_HASH, PARENT_NODE_HASH, PARENT_SOURCE_CANDIDATE_ID,
        PARENT_SOURCE_CANDIDATE_HASH, PARENT_CODE_COMMIT, PARENT_CODE_FINGERPRINT)


def release_contract():
    parent = parent_policy.release_contract()
    adapter = 'declared-numbered-fixed-policy-adapter@1'
    inputs = parent.input_contracts + (() if adapter in parent.input_contracts else (adapter,))
    draft = NumberedStrategyRelease(78, parent.executor_strategy_id, 78, parent.evaluation_interval,
        inputs + (PRIOR_POSITION_HIGH_REENTRY_INPUT,),
        parent.rule_set_contracts + (PRIOR_POSITION_HIGH_REENTRY_RULE,), BEHAVIOR, '')
    result = replace(draft, approved_digest=draft.digest())
    result.verify()
    return result


def verify_exact_parent(source):
    return verify_reentry_parent(source, parent_binding())


def derive_strategy_seventy_eight_configuration(source, *, approved_code_commit,
                                                approved_code_fingerprint, approval_reference):
    result = derive_reentry_configuration(source, binding=parent_binding(), release=release_contract(),
        approved_code_commit=approved_code_commit, approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)
    result['source_candidate_id'] = 'strategy-seventy-eight-from:' + PARENT_REVISION_ID
    return result


def verify_prepared_strategy_seventy_eight_manifest(strategy):
    release = release_contract()
    if (type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != release.number
            or type(strategy.get('revision')) is not int or strategy['revision'] != release.executor_revision
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Reentry successor identity differs')
    manifest = strategy.get('numbered_release')
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY,
                'prior_position_high_reentry_policy': REENTRY_POLICY}
    metadata = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
        'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash', 'manifest_hash'}
    if type(manifest) is not dict or set(manifest) != metadata | set(policies):
        raise ValueError('Reentry successor manifest shape differs')
    if (canonical_json(manifest['contract']) != canonical_json(release.canonical_payload())
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != PARENT_REVISION_ID or manifest['source_payload_hash'] != PARENT_PAYLOAD_HASH
            or any(canonical_json(manifest[k]) != canonical_json(v) for k,v in policies.items())
            or manifest['publication_mode'] != 'backtest_only'
            or type(manifest['approved_code_commit']) is not str or re.fullmatch('[0-9a-f]{40}',manifest['approved_code_commit']) is None
            or type(manifest['approved_code_fingerprint']) is not str or re.fullmatch('[0-9a-f]{64}',manifest['approved_code_fingerprint']) is None
            or type(manifest['approval_reference']) is not str or not 1 <= len(manifest['approval_reference'].strip()) <= 512
            or manifest['manifest_hash'] != sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()):
        raise ValueError('Reentry successor policy or source approval differs')
    return manifest


def verify_strategy_seventy_eight_manifest(strategy):
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    manifest = verify_prepared_strategy_seventy_eight_manifest(strategy)
    if numbered_strategy(78) != release_contract():
        raise ValueError('Installed reentry release differs')
    fixed_strategy_executor(release_contract().executor_strategy_id,78).verify()
    return manifest
