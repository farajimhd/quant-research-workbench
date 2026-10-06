"""Bind numeric activation to installed and independently verified declarations."""
from collections.abc import Mapping

from .drawdown_measure_policy import POLICY_ID, DrawdownMeasurePolicy, validate_drawdown_policy


def declared_drawdown_policy(release, contract, strategy):
    """Pure binding after the owning release verifier has verified its manifest."""
    release.verify()
    manifest = strategy.get("numbered_release")
    if (not isinstance(manifest, Mapping)
            or manifest.get("contract") != release.canonical_payload()
            or manifest.get("approved_digest") != release.approved_digest
            or strategy.get("strategy_id") != release.executor_strategy_id
            or strategy.get("revision") != release.executor_revision
            or strategy.get("strategy_number") != release.number
            or contract.strategy_id != release.executor_strategy_id
            or contract.strategy_number != release.number):
        raise ValueError("Drawdown activation differs from the installed release")
    inputs = tuple(v for v in release.input_contracts if v.startswith("portfolio.drawdown."))
    rules = tuple(v for v in release.rule_set_contracts if v.startswith("portfolio.drawdown."))
    policy = validate_drawdown_policy(getattr(contract, "drawdown_measure_policy", None))
    payload = manifest.get("drawdown_measure_policy")
    if not inputs and not rules:
        if policy is not None or payload is not None:
            raise ValueError("Legacy release cannot activate a drawdown measure policy")
        return None
    if (inputs != (POLICY_ID,) or rules != (POLICY_ID,)
            or type(policy) is not DrawdownMeasurePolicy or payload != policy.payload()):
        raise ValueError("Drawdown requires paired exact input, rule and manifest policy")
    return policy


def resolve_drawdown_policy(strategy_id, revision, strategy_configuration=None):
    """Fresh and resumed actors share the same installed release authority.

    No currently installed release declares this policy. Missing configuration
    cannot authorize a newly declared policy or a caller-injected engine.
    """
    if strategy_configuration is not None and not isinstance(strategy_configuration, Mapping):
        raise ValueError("Drawdown configuration must be a sealed strategy mapping")
    from .numbered_fixed_strategy import is_numbered_fixed_strategy, resolve_numbered_fixed_strategy
    if not is_numbered_fixed_strategy(strategy_id, revision):
        if strategy_configuration and (
                strategy_configuration.get("drawdown_measure_policy") is not None
                or (isinstance(strategy_configuration.get("numbered_release"), Mapping)
                    and strategy_configuration["numbered_release"].get("drawdown_measure_policy") is not None)):
            raise ValueError("Drawdown activation requires installed numbered authority")
        return None
    contract = resolve_numbered_fixed_strategy(strategy_id, revision)
    if strategy_configuration is not None and not isinstance(strategy_configuration, Mapping):
        raise ValueError("Drawdown configuration must be a sealed strategy mapping")
    manifest = strategy_configuration.get("numbered_release") if strategy_configuration is not None else None
    declared_payload = manifest.get("drawdown_measure_policy") if isinstance(manifest, Mapping) else None
    contract_payload = manifest.get("contract", {}) if isinstance(manifest, Mapping) else {}
    claims = (strategy_configuration is not None and strategy_configuration.get("drawdown_measure_policy") is not None) or declared_payload is not None
    if isinstance(contract_payload, Mapping):
        claims = claims or any(isinstance(v, str) and v.startswith("portfolio.drawdown.")
            for key in ("input_contracts", "rule_set_contracts")
            for v in contract_payload.get(key, ()))
    if getattr(contract, "drawdown_measure_policy", None) is None and not claims:
        return None  # Existing actors retain their original configuration checks.
    if strategy_configuration is None:
        if getattr(contract, "drawdown_measure_policy", None) is not None:
            raise ValueError("Declared drawdown activation requires its sealed configuration")
        return None
    if (strategy_configuration.get("strategy_id") != strategy_id
            or strategy_configuration.get("revision") != revision
            or strategy_configuration.get("drawdown_measure_policy") is not None):
        raise ValueError("Drawdown configuration disagrees with its run identity")
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    if not is_numbered_fixed_configuration({"strategy": strategy_configuration}):
        raise ValueError("Drawdown configuration lacks its installed manifest verifier")
    # Strategy1 has no later numbered manifest. It remains the original path.
    if "numbered_release" not in strategy_configuration:
        if getattr(contract, "drawdown_measure_policy", None) is not None:
            raise ValueError("Drawdown configuration has no numbered manifest")
        return None
    from .strategy_registry import numbered_strategy
    return declared_drawdown_policy(numbered_strategy(contract.strategy_number), contract, strategy_configuration)


def drawdown_policy_from_release(release):
    """Derive behavior exclusively from paired immutable contract declarations."""
    release.verify()
    inputs = tuple(v for v in release.input_contracts if v.startswith('portfolio.drawdown.'))
    rules = tuple(v for v in release.rule_set_contracts if v.startswith('portfolio.drawdown.'))
    if not inputs and not rules:
        return None
    if inputs != (POLICY_ID,) or rules != (POLICY_ID,):
        raise ValueError('Drawdown requires paired exact input and rule declarations')
    return DrawdownMeasurePolicy()


from dataclasses import dataclass
from hashlib import sha256
import json
from .journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class RunBoundDrawdownAuthority:
    """Owner-created authority over one verified persisted configuration revision.

    Full payload is canonical immutable text, not a mutable caller policy flag.
    No existing published release can construct selected authority.
    """
    run_id: str
    strategy_id: str
    strategy_revision: int
    configuration_hash: str
    configuration_revision_json: str
    policy: DrawdownMeasurePolicy

    def __post_init__(self):
        if (type(self.run_id) is not str or not self.run_id
                or type(self.strategy_revision) is not int
                or type(self.strategy_id) is not str or not self.strategy_id):
            raise ValueError('Drawdown owner lacks exact run identity')
        validate_drawdown_policy(self.policy)
        revision = json.loads(self.configuration_revision_json)
        payload = revision.get('payload')
        if (not isinstance(payload, dict) or not revision.get('revision_id')
                or revision.get('content_hash') != self.configuration_hash
                or sha256(canonical_json(payload).encode()).hexdigest() != self.configuration_hash):
            raise ValueError('Drawdown owner configuration differs from its persisted hash')
        selected = resolve_drawdown_policy(self.strategy_id, self.strategy_revision, payload.get('strategy'))
        if selected is None or selected != self.policy:
            raise ValueError('Drawdown owner lacks an installed sealed declaration')

    def verify(self, *, run_id, expected_config, configuration_hash=None):
        if (run_id != self.run_id or not isinstance(expected_config, Mapping)
                or expected_config.get('strategy_id') != self.strategy_id
                or expected_config.get('strategy_revision') != self.strategy_revision
                or configuration_hash is not None and configuration_hash != self.configuration_hash
                or expected_config.get("configuration_hash", self.configuration_hash) != self.configuration_hash):
            raise ValueError('Drawdown owner differs from committed run configuration')
        revision = json.loads(self.configuration_revision_json)
        if (revision.get('content_hash') != self.configuration_hash
                or sha256(canonical_json(revision['payload']).encode()).hexdigest() != self.configuration_hash
                or revision['payload']['strategy'].get('strategy_id') != self.strategy_id
                or revision['payload']['strategy'].get('revision') != self.strategy_revision):
            raise ValueError('Drawdown owner immutable payload changed')
        validate_drawdown_policy(self.policy)
        return self.policy


def bind_run_drawdown_authority(*, run_id, expected_config, configuration_hash,
                                configuration_revision=None):
    """Called before typed writes by both fresh and resumed journal owners."""
    if not isinstance(expected_config, Mapping):
        raise ValueError('Drawdown owner needs committed flat configuration')
    strategy_id = expected_config.get('strategy_id')
    revision = expected_config.get('strategy_revision')
    strategy = (configuration_revision.get('payload', {}).get('strategy')
                if isinstance(configuration_revision, Mapping) else None)
    policy = resolve_drawdown_policy(strategy_id, revision, strategy)
    if policy is None:
        return None
    if not isinstance(configuration_revision, Mapping):
        raise ValueError('Selected drawdown owner requires persisted full configuration')
    result = RunBoundDrawdownAuthority(run_id, strategy_id, revision, configuration_hash,
        canonical_json(dict(configuration_revision)), policy)
    result.verify(run_id=run_id, expected_config=expected_config,
                  configuration_hash=configuration_hash)
    return result


def projection_drawdown_policy(authority, *, run_id, expected_config):
    """A projector accepts owner authority, never policy from journal metadata."""
    if authority is not None:
        if type(authority) is not RunBoundDrawdownAuthority:
            raise ValueError('Projection requires exact run-bound drawdown authority')
        return authority.verify(run_id=run_id, expected_config=expected_config)
    if isinstance(expected_config, Mapping):
        selected = resolve_drawdown_policy(expected_config.get('strategy_id'),
            expected_config.get('strategy_revision'))
        if selected is not None:
            raise ValueError('Selected projection has no persisted drawdown authority')
    return None
