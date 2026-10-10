"""Native declaration selector; this module grants no execution capability."""
from src.trading_runtime.publication_source_reuse_policy import (
    INPUT, RULE, PARAMETER, parse_declared_publication_source_reuse,
)
from contextlib import contextmanager
from contextvars import ContextVar
from src.trading_runtime.journal_contract import canonical_json

_PUBLICATION = ContextVar('single_fixed_lot_publication_verification', default=None)


def selected_publication_source_reuse_policy(source):
    payload = source.installed_payload
    if payload is None:
        return None
    if type(payload) is not dict or type(payload.get('strategy')) is not dict:
        raise ValueError('Publication reuse requires a complete installed payload')
    strategy = payload['strategy']
    parameters = strategy.get('parameters')
    declaration = strategy.get('numbered_release', {}).get('contract', {})
    if type(parameters) is not dict or type(declaration) is not dict:
        raise ValueError('Publication reuse requires canonical strategy declarations')
    claimed = (PARAMETER in parameters or INPUT in declaration.get('input_contracts', ())
               or RULE in declaration.get('rule_set_contracts', ()))
    if not claimed:
        return None
    payload_image = canonical_json(payload)
    from .backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.trading_runtime.fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if (strategy.get('strategy_number') != release.number
            or strategy.get('revision') != release.number
            or source._revision != release.number
            or strategy.get('strategy_id') != source._strategy_id
            or declaration != release.canonical_payload()):
        raise ValueError('Publication reuse differs from installed sealed release')
    policy = parse_declared_publication_source_reuse(release, parameters.get(PARAMETER))
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    if (type(factory) is not FixedStructuralLotSelectedExitStrategyContract
            or factory.release != release or factory.publication_source_reuse_policy != policy):
        raise ValueError('Publication reuse differs from exact registered typed factory')
    factory.__post_init__()
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if canonical_json(source.installed_payload) != payload_image:
        raise ValueError('Publication installed payload changed during selection')
    return policy


@contextmanager
def publication_source_scope(context):
    """A single writer transaction; not selected or called by a strategy yet."""
    from src.trading_runtime.fixed_structural_lot_entry_v4 import FixedStructuralLotPublicationContext
    if type(context) is not FixedStructuralLotPublicationContext:
        raise ValueError('Exact publication context required')
    if _PUBLICATION.get() is not None:
        raise ValueError('Nested publication source verification scope')
    policy = selected_publication_source_reuse_policy(context.source)
    if policy is None:
        yield
        return
    from src.trading_runtime.single_publication_verification import SinglePublicationVerification
    from .backtest_fixed_structural_lot_source import _source_identity
    from .backtest_fixed_lot_management_reuse import (
        _content, _policy_snapshot, _entry_content, _entry_source_facts, _entry_dependencies,
    )
    source = context.source
    factory = _policy_snapshot(source)
    functions = (selected_publication_source_reuse_policy, publication_source_scope.__wrapped__,
        verified_publication_source, _source_identity, _content, _policy_snapshot,
        _entry_content, _entry_source_facts, _entry_dependencies,
        type(context)._verify_source_complete, type(context).verify_source,
        SinglePublicationVerification.verify, SinglePublicationVerification._require,
        SinglePublicationVerification._check_images, SinglePublicationVerification._image)
    codes = tuple((fn, fn.__code__) for fn in functions)
    dependencies = None

    def authority():
        source.require_prepared_source()
        source.require_installed_admission()
        if (context.source is not source or any(fn.__code__ is not code for fn, code in codes)
                or _policy_snapshot(source, factory[6]) != factory):
            raise ValueError('Publication source or factory dependency changed')

    def image():
        return _content((context.unit, context.record, _source_identity(source))).encode()

    def output(request):
        nonlocal dependencies
        current = _entry_dependencies(request)
        if dependencies is None:
            dependencies = current
        elif len(current) != len(dependencies) or any(a is not b for a, b in zip(current, dependencies)):
            raise ValueError('Publication entry source dependency changed')
        return _content((_entry_content(request), _entry_source_facts(request))).encode()

    # Deliberately run the original complete verifier before issuing the scope.
    memo = SinglePublicationVerification(original=context._verify_source_complete,
        input_image=image, output_image=output, require_authority=authority,
        max_image_bytes=policy.max_image_bytes)
    with memo.scope():
        memo.verify()
        token = _PUBLICATION.set((context, memo))
        try:
            yield
        finally:
            _PUBLICATION.reset(token)


def verified_publication_source(context, loader):
    """Keep foreign and ordinary cold readers outside the writer memo."""
    active = _PUBLICATION.get()
    if active is None:
        return loader()
    if active[0] is context:
        return active[1].verify()
    token = _PUBLICATION.set(None)
    try:
        from .backtest_fixed_lot_initial_recovery_reuse import _cold_loader
        return _cold_loader(loader)
    finally:
        _PUBLICATION.reset(token)
