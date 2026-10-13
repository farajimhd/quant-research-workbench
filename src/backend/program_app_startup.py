"""Explicit operator startup wiring into the existing app lifecycle.

The supplied service carries approved immutable pins and managed readers.
Nothing discovers credentials, publishes tables or rows, registers executors,
prepares a writer, starts producers, or launches a backtest here.
"""
from fastapi import FastAPI

from .program_configuration_selection_service import (
    ProgramConfigurationSelectionService, install_program_configuration_selection_service,
    program_configuration_selection_service,
)

_STATE_KEY = 'approved_program_selection_startup'


def configure_program_app_startup(application, service):
    """Operator composition before app startup; no HTTP installation endpoint.

    Executable registration and persisted releases must already be approved.
    Their native verification is performed by the supplied central readers
    during startup, before exposing the selection to app requests.
    """
    if type(application) is not FastAPI or type(service) is not ProgramConfigurationSelectionService:
        raise ValueError('Programme startup requires the native app and central selection service')
    prior = getattr(application.state, _STATE_KEY, None)
    installed = program_configuration_selection_service()
    if ((prior is not None and prior is not service)
            or (installed is not None and installed is not service)):
        raise ValueError('Programme startup cannot replace an approved app authority')
    setattr(application.state, _STATE_KEY, service)
    return application


async def start_program_app_selection(application):
    """Read all supplied central seals, then install; default startup is inert."""
    service = getattr(application.state, _STATE_KEY, None)
    if service is None:
        return 0
    if type(application) is not FastAPI or type(service) is not ProgramConfigurationSelectionService:
        raise ValueError('Programme startup has an invalid native authority')
    installed = program_configuration_selection_service()
    if installed is not None and installed is not service:
        raise ValueError('Programme startup cannot replace the installed authority')
    # Native snapshots verify the normalized selection, programme release,
    # executable pins and portfolio-policy attestations with owned readers.
    # No installation occurs if any read fails or is cancelled.
    options = await service.options()
    if (getattr(application.state, _STATE_KEY, None) is not service
            or program_configuration_selection_service() is not installed):
        raise RuntimeError('Programme startup owner changed during central verification')
    if (len(options) != len(service.pins)
            or {row['revision_id'] for row in options} !=
                {'strategy-configuration:' + identity for identity in service.pins}
            or any(row['selection_hash'] != service.pins[
                row['revision_id'].removeprefix('strategy-configuration:')] for row in options)):
        raise ValueError('Programme startup snapshots differ from approved pins')
    install_program_configuration_selection_service(service)
    return len(options)
