from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Callable


StrategyFactory = Callable[[list[Any]], Any]
ParameterResolver = Callable[[dict[str, Any] | None], dict[str, Any]]
DefinitionFactory = Callable[[], dict[str, Any]]
InputCatalogFactory = Callable[[], list[dict[str, Any]]]
TimeframeResolver = Callable[[dict[str, Any]], set[str]]
ObservationProjector = Callable[[Any, str], dict[str, Any]]
AssignmentEvaluator = Callable[[Any, Any], Any]


@dataclass(frozen=True, slots=True)
class NumberedStrategyRelease:
    """One complete, sealed trading behavior; executor revision is internal.

    A new entry, exit, sizing, timing, or rule-set behavior needs a new number.
    Dependencies may have separate technical versions, but each release pins
    them exactly. The digest is an integrity seal, not an external signature.
    """

    number: int
    executor_strategy_id: str
    executor_revision: int
    evaluation_interval: str
    input_contracts: tuple[str, ...]
    rule_set_contracts: tuple[str, ...]
    behavior_specification: str
    approved_digest: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "executor_strategy_id": self.executor_strategy_id,
            "executor_revision": self.executor_revision,
            "evaluation_interval": self.evaluation_interval,
            "input_contracts": list(self.input_contracts),
            "rule_set_contracts": list(self.rule_set_contracts),
            "behavior_specification": self.behavior_specification,
        }

    def digest(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return sha256(raw).hexdigest()

    def verify(self) -> None:
        fixed = re.fullmatch(r"([1-9]\d*)ms", self.evaluation_interval)
        interval_valid = (self.evaluation_interval == "events"
                          or fixed is not None and int(fixed.group(1)) % 100 == 0)
        if (type(self.number) is not int or self.number < 1
                or not self.executor_strategy_id
                or self.executor_strategy_id.strip() != self.executor_strategy_id
                or self.executor_revision < 1
                or not interval_valid
                or not self.input_contracts or len(set(self.input_contracts)) != len(self.input_contracts)
                or not self.rule_set_contracts
                or len(set(self.rule_set_contracts)) != len(self.rule_set_contracts)
                or not self.behavior_specification.strip()
                or self.approved_digest != self.digest()):
            raise ValueError("Numbered Strategy release lacks an exact approved seal")


@dataclass(frozen=True, slots=True)
class StrategyExecutorRegistration:
    """Installed, code-reviewed execution authority for one immutable Strategy revision."""

    strategy_id: str
    revision: int
    implementation: str
    definition_factory: DefinitionFactory
    parameter_resolver: ParameterResolver
    strategy_factory: StrategyFactory
    input_catalog_factory: InputCatalogFactory
    timeframe_resolver: TimeframeResolver
    observation_projector: ObservationProjector
    assignment_evaluator: AssignmentEvaluator
    executor_schema_version: int = 1

    @property
    def key(self) -> tuple[str, int]:
        return (self.strategy_id, self.revision)

    def definition(self) -> dict[str, Any]:
        definition = dict(self.definition_factory())
        if (
            str(definition.get("strategy_id") or "") != self.strategy_id
            or int(definition.get("revision") or 0) != self.revision
            or str(definition.get("implementation") or "") != self.implementation
        ):
            raise ValueError(
                f"Registered Strategy definition does not match executor {self.strategy_id}@{self.revision}"
            )
        definition["executor"] = {
            "installed": True,
            "schema_version": self.executor_schema_version,
            "key": f"{self.strategy_id}@{self.revision}",
        }
        return definition


@dataclass(frozen=True, slots=True)
class FixedStrategyExecutorRegistration:
    """Installed completed-boundary executor, deliberately outside legacy callbacks.

    This is a code catalog entry, not publication or live approval. Selection
    still requires the independently sealed normalized configuration release.
    """

    strategy_id: str
    revision: int
    evaluation_interval: str
    contract_factory: Callable[[], Any]
    strategy_factory: StrategyFactory
    mode: str = "backtest"

    @property
    def key(self) -> tuple[str, int]:
        return self.strategy_id, self.revision

    def verify(self) -> None:
        if (not self.strategy_id or type(self.revision) is not int or self.revision < 1
                or self.mode != "backtest" or not callable(self.contract_factory)
                or not callable(self.strategy_factory)):
            raise ValueError("Fixed executor needs an installed Backtest-only contract")
        contract = self.contract_factory()
        if (contract.strategy_id, contract.strategy_number, contract.execution_interval) != (
                self.strategy_id, self.revision, self.evaluation_interval):
            raise ValueError("Fixed executor identity differs from its installed contract")

    def build(self, assignments: list[Any], *, mode: str) -> Any:
        self.verify()
        if mode != self.mode:
            raise ValueError("Numbered fixed executor is Backtest-only")
        if not assignments or any((row.strategy_id, row.strategy_revision) != self.key
                                  for row in assignments):
            raise ValueError("Fixed executor assignments differ from the installed identity")
        return self.strategy_factory(assignments)


_LOCK = RLock()
_REGISTRY: dict[tuple[str, int], StrategyExecutorRegistration] = {}
_NUMBERED_RELEASES: dict[int, NumberedStrategyRelease] = {}
_BUILTIN_REGISTRY: dict[tuple[str, int], StrategyExecutorRegistration] = {}
_BUILTINS_REGISTERED = False
_FIXED_REGISTRY: dict[tuple[str, int], FixedStrategyExecutorRegistration] = {}
_NUMBERED_FIXED_REGISTERED = False


def register_numbered_strategy(release: NumberedStrategyRelease) -> None:
    """Publish once; there is deliberately no replace or mutable edit API.

    New strategy authors must follow docs/architecture/STRATEGY_CREATION_STANDARD.md.
    In particular, a changed shared rule or execution clock is a changed
    trading behavior: register the next number, never repoint this release.
    """
    release.verify()
    _ensure_builtin_executors()
    with _LOCK:
        key = (release.executor_strategy_id.strip(), release.executor_revision)
        if key not in _REGISTRY and key not in _FIXED_REGISTRY:
            raise ValueError("Numbered Strategy release lacks an installed executor")
        if key in _FIXED_REGISTRY:
            fixed = _FIXED_REGISTRY[key]
            fixed.verify()
            if fixed.evaluation_interval != release.evaluation_interval:
                raise ValueError("Numbered release differs from installed fixed execution clock")
        prior = _NUMBERED_RELEASES.get(release.number)
        if prior is not None and prior != release:
            raise ValueError(f"Strategy {release.number} is immutable; assign a new number")
        _NUMBERED_RELEASES[release.number] = release


def numbered_strategy_parent(number: int) -> int:
    """Explicit immutable inheritance; Strategy 8 branches from 6, not 7."""
    parents = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 6, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13, 15: 14, 16: 15, 17: 14, 18: 17, 19: 18, 20: 19, 21: 20, 22: 21, 23: 22, 24: 23, 25: 24, 26: 25, 27: 26, 28: 27, 29: 28, 30: 29, 31: 30, 32: 31, 33: 32, 34: 33, 35: 34, 36: 35, 37: 36, 38: 37, 39: 38, 40: 39, 41: 40, 42: 41, 46: 42, 47: 46, 48: 42, 49: 42, 50: 42, 51: 42, 52: 50, 53: 50, 54: 50, 55: 50, 56: 50, 57: 50, 58: 50, 59: 50, 60: 50, 61: 50, 64: 42, 65: 42, 66: 42, 68: 42, 69: 68, 70: 69, 71: 70, 72: 70, 73: 72, 74: 73, 77: 42, 80: 42, 81: 42, 82: 42, 83: 42, 84: 42}
    if type(number) is not int or number not in parents:
        raise ValueError("Numbered strategy has no admitted parent")
    return parents[number]


def installed_numbered_fixed_strategy_numbers() -> tuple[int, ...]:
    """Enumerate only sealed releases with their matching installed executor."""
    initialize_numbered_fixed_strategies()
    with _LOCK:
        numbers = []
        for number, release in sorted(_NUMBERED_RELEASES.items()):
            key = (release.executor_strategy_id, release.executor_revision)
            fixed = _FIXED_REGISTRY.get(key)
            if fixed is None:
                continue
            release.verify()
            fixed.verify()
            if (release.number != number or release.executor_revision != number
                    or fixed.evaluation_interval != release.evaluation_interval):
                raise ValueError("Installed numbered inventory differs from its source seals")
            numbers.append(number)
        return tuple(numbers)


def numbered_strategy(number: int) -> NumberedStrategyRelease:
    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, 71, 72, 73, 74, 77, 80, 81, 82, 83, 84):
        initialize_numbered_fixed_strategies()
    with _LOCK:
        release = _NUMBERED_RELEASES.get(number)
    if release is None:
        raise ValueError(f"Strategy {number} is not published")
    release.verify()
    return release


def register_fixed_strategy_executor(registration: FixedStrategyExecutorRegistration) -> None:
    """Install once without a replace API or any legacy callback fabrication."""
    registration.verify()
    with _LOCK:
        if registration.key in _REGISTRY:
            raise ValueError("Fixed executor identity collides with a legacy executor")
        prior = _FIXED_REGISTRY.get(registration.key)
        if prior is not None and prior != registration:
            raise ValueError("Installed fixed executor cannot be replaced")
        _FIXED_REGISTRY[registration.key] = registration


def fixed_strategy_executor(strategy_id: str, revision: int) -> FixedStrategyExecutorRegistration:
    initialize_numbered_fixed_strategies()
    with _LOCK:
        registration = _FIXED_REGISTRY.get((strategy_id, revision))
    if registration is None:
        raise ValueError("No installed fixed Strategy executor matches the selected identity")
    registration.verify()
    return registration


def _strategy_two_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(2)


def _strategy_three_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(3)


def _strategy_four_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(4)


def _strategy_five_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(5)


def _strategy_six_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(6)


def _strategy_seven_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(7)

def _strategy_eight_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(8)



def _strategy_nine_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(9)


def _strategy_ten_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(10)


def _strategy_fourteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(14)

def _strategy_fifteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(15)

def _strategy_seventeen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(17)


def _strategy_eighteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(18)


def _strategy_nineteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(19)


def _strategy_twenty_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(20)


def _strategy_twenty_one_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(21)


def _strategy_twenty_two_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(22)


def _strategy_twenty_three_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(23)


def _strategy_twenty_four_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(24)


def _strategy_thirty_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(30)


def _strategy_forty_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(40)


def _strategy_forty_one_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(41)


def _strategy_forty_two_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(42)


def _strategy_thirty_nine_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(39)


def _strategy_thirty_eight_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(38)


def _strategy_thirty_seven_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(37)


def _strategy_thirty_six_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(36)


def _strategy_thirty_five_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(35)


def _strategy_thirty_four_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(34)


def _strategy_thirty_three_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(33)


def _strategy_thirty_two_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(32)


def _strategy_thirty_one_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(31)


def _strategy_twenty_nine_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(29)


def _strategy_twenty_eight_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(28)


def _strategy_twenty_seven_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(27)


def _strategy_twenty_six_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(26)


def _strategy_twenty_five_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(25)


def _strategy_sixteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(16)


def _strategy_thirteen_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(13)


def _strategy_twelve_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(12)


def _strategy_eleven_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(11)


def _strategy_two_factory(assignments):
    from .strategy_one_runtime import AssignedStrategyOne
    return AssignedStrategyOne(assignments)


def initialize_numbered_fixed_strategies() -> None:
    """Load installed source seals independently of any database publication.

    Called by lookups/preflight; it neither publishes configuration nor enables
    a runtime. Delayed imports avoid registry/runtime import cycles.
    """
    global _NUMBERED_FIXED_REGISTERED
    with _LOCK:
        if _NUMBERED_FIXED_REGISTERED:
            return
        from .strategy_two_release import release_contract
        release = release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=release.executor_strategy_id, revision=release.executor_revision,
            evaluation_interval=release.evaluation_interval,
            contract_factory=_strategy_two_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(release)
        from .strategy_three_release import release_contract as third_release_contract
        third = third_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=third.executor_strategy_id, revision=third.executor_revision,
            evaluation_interval=third.evaluation_interval,
            contract_factory=_strategy_three_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(third)
        from .strategy_four_release import release_contract as fourth_release_contract
        fourth = fourth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fourth.executor_strategy_id, revision=fourth.executor_revision,
            evaluation_interval=fourth.evaluation_interval,
            contract_factory=_strategy_four_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fourth)
        from .strategy_five_release import release_contract as fifth_release_contract
        fifth = fifth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fifth.executor_strategy_id, revision=fifth.executor_revision,
            evaluation_interval=fifth.evaluation_interval,
            contract_factory=_strategy_five_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fifth)
        from .strategy_six_release import release_contract as sixth_release_contract
        sixth = sixth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=sixth.executor_strategy_id, revision=sixth.executor_revision,
            evaluation_interval=sixth.evaluation_interval,
            contract_factory=_strategy_six_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(sixth)
        from .strategy_seven_release import release_contract as seventh_release_contract
        seventh = seventh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=seventh.executor_strategy_id, revision=seventh.executor_revision,
            evaluation_interval=seventh.evaluation_interval,
            contract_factory=_strategy_seven_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(seventh)
        from .strategy_eight_release import release_contract as eighth_release_contract
        eighth = eighth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=eighth.executor_strategy_id, revision=eighth.executor_revision,
            evaluation_interval=eighth.evaluation_interval,
            contract_factory=_strategy_eight_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(eighth)
        from .strategy_nine_release import release_contract as ninth_release_contract
        ninth = ninth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=ninth.executor_strategy_id, revision=ninth.executor_revision,
            evaluation_interval=ninth.evaluation_interval,
            contract_factory=_strategy_nine_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(ninth)
        from .strategy_ten_release import release_contract as tenth_release_contract
        tenth = tenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=tenth.executor_strategy_id, revision=tenth.executor_revision,
            evaluation_interval=tenth.evaluation_interval,
            contract_factory=_strategy_ten_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(tenth)
        from .strategy_eleven_release import release_contract as eleventh_release_contract
        eleventh = eleventh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=eleventh.executor_strategy_id, revision=eleventh.executor_revision,
            evaluation_interval=eleventh.evaluation_interval,
            contract_factory=_strategy_eleven_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(eleventh)
        from .strategy_twelve_release import release_contract as twelfth_release_contract
        twelfth = twelfth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twelfth.executor_strategy_id, revision=twelfth.executor_revision,
            evaluation_interval=twelfth.evaluation_interval,
            contract_factory=_strategy_twelve_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twelfth)
        from .strategy_thirteen_release import release_contract as thirteenth_release_contract
        thirteenth = thirteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirteenth.executor_strategy_id, revision=thirteenth.executor_revision,
            evaluation_interval=thirteenth.evaluation_interval,
            contract_factory=_strategy_thirteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirteenth)
        from .strategy_fourteen_release import release_contract as fourteenth_release_contract
        fourteenth = fourteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fourteenth.executor_strategy_id, revision=fourteenth.executor_revision,
            evaluation_interval=fourteenth.evaluation_interval,
            contract_factory=_strategy_fourteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fourteenth)
        from .strategy_fifteen_release import release_contract as fifteenth_release_contract
        fifteenth = fifteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fifteenth.executor_strategy_id, revision=fifteenth.executor_revision,
            evaluation_interval=fifteenth.evaluation_interval,
            contract_factory=_strategy_fifteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fifteenth)
        from .strategy_sixteen_release import release_contract as sixteenth_release_contract
        sixteenth = sixteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=sixteenth.executor_strategy_id, revision=sixteenth.executor_revision,
            evaluation_interval=sixteenth.evaluation_interval,
            contract_factory=_strategy_sixteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(sixteenth)
        from .strategy_seventeen_release import release_contract as seventeenth_release_contract
        seventeenth = seventeenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=seventeenth.executor_strategy_id, revision=seventeenth.executor_revision,
            evaluation_interval=seventeenth.evaluation_interval,
            contract_factory=_strategy_seventeen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(seventeenth)
        from .strategy_eighteen_release import release_contract as eighteenth_release_contract
        eighteenth = eighteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=eighteenth.executor_strategy_id, revision=eighteenth.executor_revision,
            evaluation_interval=eighteenth.evaluation_interval,
            contract_factory=_strategy_eighteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(eighteenth)
        from .strategy_nineteen_release import release_contract as nineteenth_release_contract
        nineteenth = nineteenth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=nineteenth.executor_strategy_id, revision=nineteenth.executor_revision,
            evaluation_interval=nineteenth.evaluation_interval,
            contract_factory=_strategy_nineteen_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(nineteenth)
        from .strategy_twenty_release import release_contract as twentieth_release_contract
        twentieth = twentieth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twentieth.executor_strategy_id, revision=twentieth.executor_revision,
            evaluation_interval=twentieth.evaluation_interval,
            contract_factory=_strategy_twenty_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twentieth)
        from .strategy_twenty_one_release import release_contract as twenty_first_release_contract
        twenty_first = twenty_first_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_first.executor_strategy_id, revision=twenty_first.executor_revision,
            evaluation_interval=twenty_first.evaluation_interval,
            contract_factory=_strategy_twenty_one_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_first)
        from .strategy_twenty_two_release import release_contract as twenty_second_release_contract
        twenty_second = twenty_second_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_second.executor_strategy_id, revision=twenty_second.executor_revision,
            evaluation_interval=twenty_second.evaluation_interval,
            contract_factory=_strategy_twenty_two_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_second)
        from .strategy_twenty_three_release import release_contract as twenty_third_release_contract
        twenty_third = twenty_third_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_third.executor_strategy_id, revision=twenty_third.executor_revision,
            evaluation_interval=twenty_third.evaluation_interval,
            contract_factory=_strategy_twenty_three_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_third)
        from .strategy_twenty_four_release import release_contract as twenty_fourth_release_contract
        twenty_fourth = twenty_fourth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_fourth.executor_strategy_id, revision=twenty_fourth.executor_revision,
            evaluation_interval=twenty_fourth.evaluation_interval,
            contract_factory=_strategy_twenty_four_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_fourth)
        from .strategy_twenty_five_release import release_contract as twenty_fifth_release_contract
        twenty_fifth = twenty_fifth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_fifth.executor_strategy_id, revision=twenty_fifth.executor_revision,
            evaluation_interval=twenty_fifth.evaluation_interval,
            contract_factory=_strategy_twenty_five_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_fifth)
        from .strategy_twenty_six_release import release_contract as twenty_sixth_release_contract
        twenty_sixth = twenty_sixth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_sixth.executor_strategy_id, revision=twenty_sixth.executor_revision,
            evaluation_interval=twenty_sixth.evaluation_interval,
            contract_factory=_strategy_twenty_six_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_sixth)
        from .strategy_twenty_seven_release import release_contract as twenty_seventh_release_contract
        twenty_seventh = twenty_seventh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_seventh.executor_strategy_id, revision=twenty_seventh.executor_revision,
            evaluation_interval=twenty_seventh.evaluation_interval,
            contract_factory=_strategy_twenty_seven_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_seventh)
        from .strategy_twenty_eight_release import release_contract as twenty_eighth_release_contract
        twenty_eighth = twenty_eighth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_eighth.executor_strategy_id, revision=twenty_eighth.executor_revision,
            evaluation_interval=twenty_eighth.evaluation_interval,
            contract_factory=_strategy_twenty_eight_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_eighth)
        from .strategy_twenty_nine_release import release_contract as twenty_nineh_release_contract
        twenty_nineh = twenty_nineh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=twenty_nineh.executor_strategy_id, revision=twenty_nineh.executor_revision,
            evaluation_interval=twenty_nineh.evaluation_interval,
            contract_factory=_strategy_twenty_nine_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(twenty_nineh)
        from .strategy_thirty_release import release_contract as thirtieth_release_contract
        thirtieth = thirtieth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirtieth.executor_strategy_id, revision=thirtieth.executor_revision,
            evaluation_interval=thirtieth.evaluation_interval,
            contract_factory=_strategy_thirty_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirtieth)
        from .strategy_thirty_one_release import release_contract as thirty_first_release_contract
        thirty_first = thirty_first_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_first.executor_strategy_id, revision=thirty_first.executor_revision,
            evaluation_interval=thirty_first.evaluation_interval,
            contract_factory=_strategy_thirty_one_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_first)
        from .strategy_thirty_two_release import release_contract as thirty_second_release_contract
        thirty_second = thirty_second_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_second.executor_strategy_id, revision=thirty_second.executor_revision,
            evaluation_interval=thirty_second.evaluation_interval,
            contract_factory=_strategy_thirty_two_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_second)
        from .strategy_thirty_three_release import release_contract as thirty_third_release_contract
        thirty_third = thirty_third_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_third.executor_strategy_id, revision=thirty_third.executor_revision,
            evaluation_interval=thirty_third.evaluation_interval,
            contract_factory=_strategy_thirty_three_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_third)
        from .strategy_thirty_four_release import release_contract as thirty_fourth_release_contract
        thirty_fourth = thirty_fourth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_fourth.executor_strategy_id, revision=thirty_fourth.executor_revision,
            evaluation_interval=thirty_fourth.evaluation_interval,
            contract_factory=_strategy_thirty_four_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_fourth)
        from .strategy_thirty_five_release import release_contract as thirty_fifth_release_contract
        thirty_fifth = thirty_fifth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_fifth.executor_strategy_id, revision=thirty_fifth.executor_revision,
            evaluation_interval=thirty_fifth.evaluation_interval,
            contract_factory=_strategy_thirty_five_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_fifth)
        from .strategy_thirty_six_release import release_contract as thirty_sixth_release_contract
        thirty_sixth = thirty_sixth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_sixth.executor_strategy_id, revision=thirty_sixth.executor_revision,
            evaluation_interval=thirty_sixth.evaluation_interval,
            contract_factory=_strategy_thirty_six_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_sixth)
        from .strategy_thirty_seven_release import release_contract as thirty_seventh_release_contract
        thirty_seventh = thirty_seventh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_seventh.executor_strategy_id, revision=thirty_seventh.executor_revision,
            evaluation_interval=thirty_seventh.evaluation_interval,
            contract_factory=_strategy_thirty_seven_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_seventh)
        from .strategy_thirty_eight_release import release_contract as thirty_eighth_release_contract
        thirty_eighth = thirty_eighth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_eighth.executor_strategy_id, revision=thirty_eighth.executor_revision,
            evaluation_interval=thirty_eighth.evaluation_interval,
            contract_factory=_strategy_thirty_eight_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_eighth)
        from .strategy_thirty_nine_release import release_contract as thirty_ninth_release_contract
        thirty_ninth = thirty_ninth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=thirty_ninth.executor_strategy_id, revision=thirty_ninth.executor_revision,
            evaluation_interval=thirty_ninth.evaluation_interval,
            contract_factory=_strategy_thirty_nine_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(thirty_ninth)
        from .strategy_forty_release import release_contract as fortieth_release_contract
        fortieth = fortieth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fortieth.executor_strategy_id, revision=fortieth.executor_revision,
            evaluation_interval=fortieth.evaluation_interval,
            contract_factory=_strategy_forty_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fortieth)
        from .strategy_forty_one_release import release_contract as forty_first_release_contract
        forty_first = forty_first_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_first.executor_strategy_id, revision=forty_first.executor_revision,
            evaluation_interval=forty_first.evaluation_interval,
            contract_factory=_strategy_forty_one_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(forty_first)
        from .strategy_forty_two_release import release_contract as forty_second_release_contract
        forty_second = forty_second_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_second.executor_strategy_id, revision=forty_second.executor_revision,
            evaluation_interval=forty_second.evaluation_interval,
            contract_factory=_strategy_forty_two_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(forty_second)
        from .strategy_forty_six_release import release_contract as forty_sixth_release_contract
        forty_sixth = forty_sixth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_sixth.executor_strategy_id, revision=46,
            evaluation_interval=forty_sixth.evaluation_interval,
            contract_factory=_strategy_forty_six_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(forty_sixth)
        from .strategy_forty_seven_release import release_contract as forty_seventh_release_contract
        forty_seventh = forty_seventh_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_seventh.executor_strategy_id, revision=47,
            evaluation_interval=forty_seventh.evaluation_interval,
            contract_factory=_strategy_forty_seven_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(forty_seventh)
        from .strategy_forty_eight_release import release_contract as forty_eighth_release_contract
        forty_eighth = forty_eighth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_eighth.executor_strategy_id, revision=48,
            evaluation_interval=forty_eighth.evaluation_interval,
            contract_factory=_strategy_forty_eight_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(forty_eighth)
        from .strategy_forty_nine_release import release_contract as forty_ninth_release_contract
        from .strategy_forty_nine_contract import strategy_forty_nine_contract, AssignedFixedSwingLadder
        forty_ninth = forty_ninth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=forty_ninth.executor_strategy_id, revision=49,
            evaluation_interval=forty_ninth.evaluation_interval,
            contract_factory=strategy_forty_nine_contract, strategy_factory=AssignedFixedSwingLadder))
        register_numbered_strategy(forty_ninth)
        from .strategy_sixty_five_release import release_contract as sixty_fifth_release_contract
        from .strategy_sixty_five_contract import strategy_sixty_five_contract, AssignedWaitingSwingLadder65
        sixty_fifth = sixty_fifth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=sixty_fifth.executor_strategy_id, revision=65,
            evaluation_interval=sixty_fifth.evaluation_interval,
            contract_factory=strategy_sixty_five_contract, strategy_factory=AssignedWaitingSwingLadder65))
        register_numbered_strategy(sixty_fifth)
        from .strategy_fifty_release import release_contract as fiftieth_release_contract
        from .strategy_fifty_contract import strategy_fifty_contract
        fiftieth = fiftieth_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fiftieth.executor_strategy_id, revision=50,
            evaluation_interval=fiftieth.evaluation_interval,
            contract_factory=strategy_fifty_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fiftieth)
        from .strategy_fifty_nine_release import release_contract as new_59_release_contract
        from .strategy_fifty_nine_contract import strategy_fifty_nine_contract
        new_59 = new_59_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_59.executor_strategy_id, revision=59, evaluation_interval=new_59.evaluation_interval,
            contract_factory=strategy_fifty_nine_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(new_59)
        from .strategy_sixty_release import release_contract as new_60_release_contract
        from .strategy_sixty_contract import strategy_sixty_contract
        new_60 = new_60_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_60.executor_strategy_id, revision=60, evaluation_interval=new_60.evaluation_interval,
            contract_factory=strategy_sixty_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(new_60)
        from .strategy_sixty_one_release import release_contract as new_61_release_contract
        from .strategy_sixty_one_contract import strategy_sixty_one_contract
        new_61 = new_61_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_61.executor_strategy_id, revision=61, evaluation_interval=new_61.evaluation_interval,
            contract_factory=strategy_sixty_one_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(new_61)
        from .strategy_fifty_one_release import release_contract as fifty_first_release_contract
        from .strategy_fifty_one_contract import strategy_fifty_one_contract, AssignedFixedSwingLadder51
        fifty_first = fifty_first_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fifty_first.executor_strategy_id, revision=51,
            evaluation_interval=fifty_first.evaluation_interval,
            contract_factory=strategy_fifty_one_contract, strategy_factory=AssignedFixedSwingLadder51))
        register_numbered_strategy(fifty_first)
        from .strategy_fifty_two_release import release_contract as fifty_second_release_contract
        from .strategy_fifty_two_contract import strategy_fifty_two_contract
        fifty_second = fifty_second_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=fifty_second.executor_strategy_id, revision=52,
            evaluation_interval=fifty_second.evaluation_interval,
            contract_factory=strategy_fifty_two_contract, strategy_factory=_strategy_two_factory))
        register_numbered_strategy(fifty_second)
        from .strategy_fifty_three_release import release_contract as new_53_release_contract
        from .strategy_fifty_three_contract import strategy_fifty_three_contract
        new_53 = new_53_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_53.executor_strategy_id, revision=53,
            evaluation_interval=new_53.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_three_contract))
        register_numbered_strategy(new_53)
        from .strategy_fifty_four_release import release_contract as new_54_release_contract
        from .strategy_fifty_four_contract import strategy_fifty_four_contract
        new_54 = new_54_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_54.executor_strategy_id, revision=54,
            evaluation_interval=new_54.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_four_contract))
        register_numbered_strategy(new_54)
        from .strategy_fifty_five_release import release_contract as new_55_release_contract
        from .strategy_fifty_five_contract import strategy_fifty_five_contract
        new_55 = new_55_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_55.executor_strategy_id, revision=55,
            evaluation_interval=new_55.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_five_contract))
        register_numbered_strategy(new_55)
        from .strategy_fifty_six_release import release_contract as new_56_release_contract
        from .strategy_fifty_six_contract import strategy_fifty_six_contract
        new_56 = new_56_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_56.executor_strategy_id, revision=56,
            evaluation_interval=new_56.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_six_contract))
        register_numbered_strategy(new_56)
        from .strategy_fifty_seven_release import release_contract as new_57_release_contract
        from .strategy_fifty_seven_contract import strategy_fifty_seven_contract
        new_57 = new_57_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_57.executor_strategy_id, revision=57,
            evaluation_interval=new_57.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_seven_contract))
        register_numbered_strategy(new_57)
        from .strategy_fifty_eight_release import release_contract as new_58_release_contract
        from .strategy_fifty_eight_contract import strategy_fifty_eight_contract
        new_58 = new_58_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_58.executor_strategy_id, revision=58,
            evaluation_interval=new_58.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_fifty_eight_contract))
        register_numbered_strategy(new_58)
        from .strategy_sixty_four_release import release_contract as new_64_release_contract
        from .strategy_sixty_four_contract import strategy_sixty_four_contract
        new_64 = new_64_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_64.executor_strategy_id, revision=new_64.executor_revision,
            evaluation_interval=new_64.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_sixty_four_contract))
        register_numbered_strategy(new_64)
        from .strategy_sixty_six_release import release_contract as new_66_release_contract
        from .strategy_sixty_six_contract import strategy_sixty_six_contract
        new_66 = new_66_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_66.executor_strategy_id, revision=new_66.executor_revision,
            evaluation_interval=new_66.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_sixty_six_contract))
        register_numbered_strategy(new_66)
        from .strategy_sixty_eight_release import release_contract as new_68_release_contract
        from .strategy_sixty_eight_contract import strategy_sixty_eight_contract
        new_68 = new_68_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_68.executor_strategy_id, revision=new_68.executor_revision,
            evaluation_interval=new_68.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_sixty_eight_contract))
        register_numbered_strategy(new_68)
        from .strategy_sixty_nine_release import release_contract as new_69_release_contract
        from .strategy_sixty_nine_contract import strategy_sixty_nine_contract
        new_69 = new_69_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_69.executor_strategy_id, revision=new_69.executor_revision,
            evaluation_interval=new_69.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_sixty_nine_contract))
        register_numbered_strategy(new_69)
        from .strategy_seventy_release import release_contract as new_70_release_contract
        from .strategy_seventy_contract import strategy_seventy_contract
        new_70 = new_70_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_70.executor_strategy_id, revision=new_70.executor_revision,
            evaluation_interval=new_70.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_contract))
        register_numbered_strategy(new_70)
        from .strategy_seventy_one_release import release_contract as new_71_release_contract
        from .strategy_seventy_one_contract import strategy_seventy_one_contract
        new_71 = new_71_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_71.executor_strategy_id, revision=new_71.executor_revision,
            evaluation_interval=new_71.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_one_contract))
        register_numbered_strategy(new_71)
        from .strategy_seventy_two_release import release_contract as new_72_release_contract
        from .strategy_seventy_two_contract import strategy_seventy_two_contract
        new_72 = new_72_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_72.executor_strategy_id, revision=new_72.executor_revision,
            evaluation_interval=new_72.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_two_contract))
        register_numbered_strategy(new_72)
        from .strategy_seventy_three_release import release_contract as new_73_release_contract
        from .strategy_seventy_three_contract import strategy_seventy_three_contract
        new_73 = new_73_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_73.executor_strategy_id, revision=new_73.executor_revision,
            evaluation_interval=new_73.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_three_contract))
        register_numbered_strategy(new_73)
        from .strategy_seventy_four_release import release_contract as new_74_release_contract
        from .strategy_seventy_four_contract import strategy_seventy_four_contract
        new_74 = new_74_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_74.executor_strategy_id, revision=new_74.executor_revision,
            evaluation_interval=new_74.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_four_contract))
        register_numbered_strategy(new_74)
        from .strategy_seventy_seven_release import release_contract as new_77_release_contract
        from .strategy_seventy_seven_contract import strategy_seventy_seven_contract
        new_77 = new_77_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_77.executor_strategy_id, revision=new_77.executor_revision,
            evaluation_interval=new_77.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_seventy_seven_contract))
        register_numbered_strategy(new_77)
        from .strategy_eighty_release import release_contract as new_80_release_contract
        from .strategy_eighty_contract import strategy_eighty_contract
        new_80 = new_80_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_80.executor_strategy_id, revision=new_80.executor_revision,
            evaluation_interval=new_80.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_eighty_contract))
        register_numbered_strategy(new_80)
        from .strategy_eighty_one_release import release_contract as new_81_release_contract
        from .strategy_eighty_one_contract import strategy_eighty_one_contract
        new_81 = new_81_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_81.executor_strategy_id, revision=new_81.executor_revision,
            evaluation_interval=new_81.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_eighty_one_contract))
        register_numbered_strategy(new_81)
        from .strategy_eighty_two_release import release_contract as new_82_release_contract
        from .strategy_eighty_two_contract import strategy_eighty_two_contract
        new_82 = new_82_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_82.executor_strategy_id, revision=new_82.executor_revision,
            evaluation_interval=new_82.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_eighty_two_contract))
        register_numbered_strategy(new_82)
        from .strategy_eighty_three_release import release_contract as new_83_release_contract
        from .strategy_eighty_three_contract import strategy_eighty_three_contract
        new_83 = new_83_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_83.executor_strategy_id, revision=new_83.executor_revision,
            evaluation_interval=new_83.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_eighty_three_contract))
        register_numbered_strategy(new_83)
        from .strategy_eighty_four_release import release_contract as new_84_release_contract
        from .strategy_eighty_four_contract import strategy_eighty_four_contract
        new_84 = new_84_release_contract()
        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
            strategy_id=new_84.executor_strategy_id, revision=new_84.executor_revision,
            evaluation_interval=new_84.evaluation_interval,
            strategy_factory=_strategy_two_factory,
            contract_factory=strategy_eighty_four_contract))
        register_numbered_strategy(new_84)
        _NUMBERED_FIXED_REGISTERED = True


def _strategy_forty_six_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(46)


def _strategy_forty_seven_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(47)


def register_strategy_executor(
    registration: StrategyExecutorRegistration,
    *,
    replace: bool = False,
) -> None:
    if not registration.strategy_id or registration.revision <= 0:
        raise ValueError("Strategy executor identity and positive revision are required")
    if not registration.implementation:
        raise ValueError("Strategy executor implementation identity is required")
    with _LOCK:
        if registration.key in _FIXED_REGISTRY:
            raise ValueError("Installed fixed executor cannot be replaced by a legacy executor")
        existing = _REGISTRY.get(registration.key)
        if existing is not registration and any(
            (release.executor_strategy_id.strip(), release.executor_revision)
            == registration.key for release in _NUMBERED_RELEASES.values()
        ):
            raise ValueError(
                f"Published Strategy executor {registration.strategy_id}@{registration.revision} "
                "cannot be replaced"
            )
        if existing is not None and existing != registration and not replace:
            raise ValueError(
                f"Strategy executor {registration.strategy_id}@{registration.revision} is already registered"
            )
        _REGISTRY[registration.key] = registration


def unregister_strategy_executor(strategy_id: str, revision: int) -> None:
    """Remove a registration for isolated tests; application code must not unload executors."""

    with _LOCK:
        key = (str(strategy_id), int(revision))
        if any((release.executor_strategy_id.strip(), release.executor_revision) == key
               for release in _NUMBERED_RELEASES.values()):
            raise ValueError("Published Strategy executor cannot be unregistered")
        _REGISTRY.pop(key, None)


def strategy_executor(
    strategy_id: str,
    revision: int,
) -> StrategyExecutorRegistration:
    _ensure_builtin_executors()
    key = (str(strategy_id or "").strip(), int(revision or 0))
    with _LOCK:
        registration = _REGISTRY.get(key)
    if registration is None:
        raise ValueError(
            f"No installed Strategy executor matches {key[0] or '<missing>'}@{key[1]}; "
            "publish an installed definition revision before starting a runtime"
        )
    return registration


def strategy_executor_optional(
    strategy_id: str,
    revision: int,
) -> StrategyExecutorRegistration | None:
    try:
        return strategy_executor(strategy_id, revision)
    except ValueError:
        return None


def installed_strategy_executors() -> tuple[StrategyExecutorRegistration, ...]:
    _ensure_builtin_executors()
    with _LOCK:
        return tuple(
            _REGISTRY[key]
            for key in sorted(_REGISTRY, key=lambda item: (item[0], item[1]))
        )


def typed_persistence_executor(
    strategy_id: str, revision: int,
) -> StrategyExecutorRegistration:
    """Admit only the original built-in executor object to inactive typed mode.

    Legacy registration and replacement remain available; typed persistence
    must fail closed until an explicit catalog exists for another executor.
    """
    registration = strategy_executor(strategy_id, revision)
    with _LOCK:
        if _BUILTIN_REGISTRY.get(registration.key) is not registration:
            raise ValueError(
                f"No typed persistence catalog for {registration.strategy_id}@{registration.revision}"
            )
    return registration


def installed_strategy_definitions() -> list[dict[str, Any]]:
    return [registration.definition() for registration in installed_strategy_executors()]


def installed_strategy_input_catalog() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for registration in installed_strategy_executors():
        for source in registration.input_catalog_factory():
            source_id = str(source.get("source_id") or "")
            if source_id:
                rows.setdefault(source_id, dict(source))
    return [rows[source_id] for source_id in sorted(rows)]


def _ensure_builtin_executors() -> None:
    global _BUILTINS_REGISTERED
    with _LOCK:
        if _BUILTINS_REGISTERED:
            return
        from src.trading_runtime.strategy_engine import (
            AssignedLongMomentumStrategy,
            HISTORICAL_STRATEGY_REVISIONS,
            LongMomentumStrategyEngine,
            STRATEGY_ID,
            STRATEGY_REVISION,
            long_momentum_strategy_definition,
            resolve_long_momentum_parameters,
            strategy_input_catalog,
            strategy_observation_source_values,
            strategy_rule_timeframes,
        )

        for revision in (*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION):
            builtin = StrategyExecutorRegistration(
                    strategy_id=STRATEGY_ID,
                    revision=revision,
                    implementation=(
                        "src.trading_runtime.strategy_engine.LongMomentumStrategyEngine"
                    ),
                    definition_factory=lambda revision=revision: long_momentum_strategy_definition(
                        revision=revision
                    ),
                    parameter_resolver=lambda parameters, revision=revision: resolve_long_momentum_parameters(
                        parameters,
                        revision=revision,
                    ),
                    strategy_factory=lambda assignments, revision=revision: AssignedLongMomentumStrategy(
                        assignments,
                        revision=revision,
                    ),
                    input_catalog_factory=strategy_input_catalog,
                    timeframe_resolver=strategy_rule_timeframes,
                    observation_projector=strategy_observation_source_values,
                    assignment_evaluator=LongMomentumStrategyEngine(
                        revision=revision
                    ).evaluate,
                )
            register_strategy_executor(builtin)
            _BUILTIN_REGISTRY[builtin.key] = builtin
        _BUILTINS_REGISTERED = True


def _strategy_forty_eight_contract():
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(48)
