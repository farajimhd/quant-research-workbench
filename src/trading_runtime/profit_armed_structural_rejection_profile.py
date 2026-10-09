"""Issued selected writer scope after authentic registered full source proof.

No credentials, grants or table installation. A policy or a content hash alone
cannot select these tables. An unpublished standalone declaration cannot issue
this profile.
"""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
import re

_ISSUED=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class NativeStructuralRejectionProfile:
    owner: object
    source_proof: str
    approved_release_digest: str


def issue_native_structural_rejection_profile(owner):
    from src.backend.backtest_profit_armed_structural_rejection_management import require_native_structural_rejection_owner
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from .strategy_registry import numbered_strategy,fixed_strategy_executor
    from .profit_armed_structural_rejection_native_policy import native_structural_rejection_declaration
    require_native_structural_rejection_owner(owner)
    contract=owner.manager.contract
    release=numbered_strategy(contract.strategy_number)
    release.verify()
    executor=fixed_strategy_executor(release.executor_strategy_id,release.executor_revision)
    executor.verify()
    registered_contract=executor.contract_factory()
    if (getattr(contract,'release',None)!=release
            or type(contract) is not type(registered_contract) or contract!=registered_contract
            or native_structural_rejection_declaration(registered_contract)!=owner.declaration
            or native_structural_rejection_declaration(contract)!=owner.declaration):
        raise ValueError('Selected writer requires actual registered own declaration')
    # Never substitute the parent proof or a caller's approval flag. Until a
    # new own source route is sealed, this real call keeps admission closed.
    proof=certify_numbered_fixed_v4_projection(contract.strategy_number)
    if type(proof) is not str or not re.fullmatch('[0-9a-f]{64}',proof):
        raise ValueError('Selected writer lacks complete own source proof')
    profile=NativeStructuralRejectionProfile(owner,proof,release.approved_digest)
    _ISSUED[profile]=(owner,contract,release,proof,owner.manager.runtime.run_id,
                     owner.manager.runtime.config.strategy_revision,owner.declaration,registered_contract)
    return profile


def require_native_structural_rejection_profile(profile,*,owner=None):
    from src.backend.backtest_profit_armed_structural_rejection_management import require_native_structural_rejection_owner
    if type(profile) is not NativeStructuralRejectionProfile or profile not in _ISSUED:
        raise ValueError('Unissued structural rejection writer profile')
    source,contract,release,proof,run,revision,declaration,registered_contract=_ISSUED[profile]
    require_native_structural_rejection_owner(source)
    if (profile.owner is not source or owner is not None and owner is not source
            or source.manager.contract is not contract or contract.release!=release
            or type(contract) is not type(registered_contract) or contract!=registered_contract
            or profile.source_proof!=proof or profile.approved_release_digest!=release.approved_digest
            or source.manager.runtime.run_id!=run or source.manager.runtime.config.strategy_revision!=revision
            or source.declaration!=declaration):
        raise ValueError('Selected profile changed its certified operation/identity')
    return profile


def selected_structural_rejection_tables(profile):
    require_native_structural_rejection_profile(profile)
    from .profit_armed_structural_rejection_snapshot import TABLES
    return TABLES
