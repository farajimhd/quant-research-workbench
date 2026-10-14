"""Execute or resume an immutable plan with frozen resources and admission."""
from . import campaign,runtime_plan,verification
from .launch import ExecutionLaunch


def run(query,database,dataset_id,claim,client_factory,dictionary_claim_factory,
        capacity_factory,*,stop=None,progress=None,storage_observer=None):
    """Factories receive the stored configuration, never current CLI defaults.

    Capacity qualification is an explicit dependency: this entry point cannot
    turn an inventory or an unqualified legacy compression ratio into admission.
    The campaign controller independently verifies persisted unit receipts when
    resuming and publishes its seal only after successful certification.
    """
    if not callable(capacity_factory):
        raise ValueError('Saved execution requires qualified capacity admission')
    claim.check()
    saved=campaign.load_plan(query,database,dataset_id)
    guard=verification.source_guard(query,saved)
    guard()
    clients=lambda:client_factory(saved.config)
    capacity=capacity_factory(saved.config,saved.units)
    if not callable(capacity):
        raise ValueError('Capacity factory must return a qualified admission guard')
    runtime=runtime_plan.runtime(saved,claim,clients,guard)
    launch=ExecutionLaunch(query,saved.config,saved.dataset_id,saved.units,saved.source_hash,
        claim,clients,guard,capacity,dictionary_claim_factory,runtime,storage_observer=storage_observer)
    if launch.controller.plan_hash!=saved.plan_hash:
        raise ValueError('Saved execution differs from its immutable stored plan')
    return launch.run(stop=stop,progress=progress)
