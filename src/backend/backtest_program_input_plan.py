"""Read-only complete-session planning over existing certified QMD cohorts."""
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from .backtest_program_inputs import preflight_program_cohort_inputs


@dataclass(frozen=True, slots=True)
class ProgramCohortInputPlan:
    pins: object
    population_pin: object
    listing_by_assignment: object
    first_ordinal: int
    stop_ordinal: int
    evaluation_block: str
    first_bucket: int
    stop_bucket: int
    as_of: datetime
    concurrency: int

    def __post_init__(self):
        object.__setattr__(self, "pins", MappingProxyType(dict(self.pins)))
        object.__setattr__(self, "listing_by_assignment", MappingProxyType(dict(self.listing_by_assignment)))


def preflight_program_session_inputs(release, cohorts, *, first_bucket, stop_bucket):
    """Verify exact coverage before any transport, calculation or publication.

    This is an input-plan proof only. Broker, source publication, configuration,
    ownership and run admission remain the existing separate launch gates.
    """
    if (type(cohorts) is not tuple or not cohorts or len(cohorts) > 57600
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000):
        raise ValueError("Programme input plan requires its bounded complete session")
    expected, common = first_bucket, None
    for cohort in cohorts:
        if type(cohort) is not ProgramCohortInputPlan or cohort.first_bucket != expected:
            raise ValueError("Programme input plan has a gap, overlap or unordered cohort")
        pins, assignments = preflight_program_cohort_inputs(release, cohort.pins, cohort.population_pin,
            cohort.listing_by_assignment, first_ordinal=cohort.first_ordinal,
            stop_ordinal=cohort.stop_ordinal, evaluation_block=cohort.evaluation_block,
            first_bucket=cohort.first_bucket, stop_bucket=cohort.stop_bucket,
            as_of=cohort.as_of, concurrency=cohort.concurrency)
        identity = (cohort.population_pin.opening.scope.session_date,
            cohort.population_pin.source_revision, tuple(sorted(assignments.items())),
            tuple(sorted((listing, pin.header["content_hash"]) for listing, pin in pins.items())),
            cohort.first_ordinal, cohort.evaluation_block)
        if common is not None and identity != common:
            raise ValueError("Programme input plan changed its source, assignments or evaluation origin")
        common, expected = identity, cohort.stop_bucket
    if expected != stop_bucket:
        raise ValueError("Programme input plan does not cover the requested session end")
    return cohorts
