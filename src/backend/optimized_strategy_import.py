"""Prepare native shared releases from verified optimizer evidence.

No strategy registration, market preparation, broker launch, or table creation
occurs here. Publication uses the existing normalized release publisher.
"""
from dataclasses import dataclass
from types import MappingProxyType

from src.trading_runtime.optimized_candidate import (
    OptimizedCandidate, compile_candidate, required_candidate_fields,
)
from src.trading_runtime.columnar_rule_program import InputField
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.market_engine.columnar_history_product import BY_NAME
from src.trading_runtime.columnar_program_input_contract import portable_revision


@dataclass(frozen=True)
class PreparedOptimizedRelease:
    candidate: OptimizedCandidate
    rows: object
    release: object
    required_fields: tuple[str, ...]


def prepare_optimized_release(candidate, *, history_code_hash, code_hash,
        universe_source_hash, strategy_number, strategy_id, release_id, published_at):
    """Use the same producer revision and normalized release as native strategies.

    Sizing remains the frozen candidate's explicit parameters. Changing sizing
    later creates a new release via the existing native project_release API.
    """
    names = sorted(required_candidate_fields(candidate))
    if not names:
        raise ValueError('An audited entry program must depend on market evidence')
    selected = tuple(sorted((BY_NAME[name] for name in names), key=lambda f: f.field_id))
    revision = portable_revision(selected, history_code_hash, volume_window_seconds=30)
    fields = MappingProxyType({f.name: InputField(f.name, f.unit,
        'q_live.qmd_causal_history_value_v1', revision, 1000) for f in selected})
    specification = compile_candidate(candidate, fields)
    rows = project_release(specification, fields, strategy_number=strategy_number,
        strategy_id=strategy_id, release_id=release_id, code_hash=code_hash,
        universe_source_hash=universe_source_hash, published_at=published_at)
    release = restore_release(rows)
    verify_imported_release(candidate, release)
    return PreparedOptimizedRelease(candidate, rows, release, tuple(names))


def verify_imported_release(candidate, release):
    """Reject a different program, sizing, universe or management policy."""
    from decimal import Decimal
    from src.trading_runtime.columnar_strategy_release import POLICY
    from src.trading_runtime.optimized_program_specification import STAGES
    expected = compile_candidate(candidate, release.fields)
    if (release.header['source_winner_hash'] != candidate.program_hash
            or release.header['source_generation'] != candidate.generation
            or release.header['source_candidate'] != candidate.candidate
            or release.programs != expected.programs
            or release.universe != expected.universe
            or dict(release.minimum_age_seconds) != {
                stage: expected.minimum_age_seconds.get(stage, 0) for stage in STAGES}
            or any(release.header[key] != expected.policy[key] for key in POLICY)
            or release.header['entry_cash'] != Decimal(str(candidate.execution['entry_dollars']))
            or release.header['add_cash'] != Decimal(str(candidate.execution['add_dollars']))):
        raise ValueError('Imported release differs from its frozen optimized candidate')
