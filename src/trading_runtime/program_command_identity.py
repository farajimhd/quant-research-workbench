"""Exact installed programme identity for generic normalized command codecs."""


def require_installed_program_identity(registration, strategy_id, revision):
    from .strategy_registry import ProgramStrategyExecutorRegistration, program_strategy_executor
    if (type(registration) is not ProgramStrategyExecutorRegistration
            or type(strategy_id) is not str or type(revision) is not int
            or registration.key != (strategy_id, revision)
            or program_strategy_executor(strategy_id, revision) is not registration):
        raise ValueError('Command lineage differs from its exact installed programme')
    registration.verify()
    return registration
