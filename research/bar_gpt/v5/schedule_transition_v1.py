"""Explicit user-authorized corrected-data schedule restart; no general resume bypass."""

from copy import deepcopy

from .quote_repair_v1 import verify_seal
from .shards.contract import digest


def prepare(saved, prepared):
    """Retain global epoch/step and AdamW moments; restart only cosine position."""
    plan = dict(
        version="bargpt-corrected-schedule-reset-v1",
        authorization="user-20261110-corrected-data-3e-4-40-epochs",
        start_epoch=saved["epoch"],
        start_step=saved["step"],
        additional_epochs=40,
        source_contract=deepcopy(saved["contract"]),
        source_scheduler=deepcopy(saved["scheduler"]),
        peak=3e-4,
        minimum=5e-5,
        warmup=0,
    )
    plan["hash"] = digest(plan)
    prepared["data_transition"]["schedule_reset"] = plan
    prepared["contract"] = planned_contract(prepared["contract"], plan)
    prepared["scheduler"] = dict(
        peak=plan["peak"], minimum=plan["minimum"], epochs=40,
        warmup=0, epoch=0, updates=0,
    )
    return plan


def planned_contract(contract, plan):
    verify_seal(plan, "hash")
    if (
        plan["version"] != "bargpt-corrected-schedule-reset-v1"
        or plan["authorization"] != "user-20261110-corrected-data-3e-4-40-epochs"
        or plan["additional_epochs"] != 40 or plan["peak"] != 3e-4
        or plan["minimum"] != 5e-5 or plan["warmup"] != 0
        or plan["start_epoch"] < 1 or plan["start_step"] < 1
    ):
        raise ValueError("Unknown corrected-data scheduler authorization")
    return dict(contract, epochs=plan["start_epoch"] + 40,
                peak=3e-4, minimum=5e-5, warmup=0)


def validate(saved):
    """Validate schedule position on initial entry and later ordinary resumes."""
    plan = saved["data_transition"].get("schedule_reset")
    if plan is None:
        return None
    planned_contract({}, plan)
    if plan["source_contract"] != saved["data_transition"]["source_contract"]:
        raise ValueError("Scheduler reset has foreign source contract")
    start, end = plan["start_epoch"], plan["start_epoch"] + 40
    schedule = saved["scheduler"]
    if (
        not start <= saved["epoch"] <= end
        or saved["step"] < plan["start_step"]
        or any(schedule[k] != v for k, v in dict(
            peak=3e-4, minimum=5e-5, epochs=40, warmup=0).items())
        or not 0 <= schedule["epoch"] < 40
        or schedule["updates"] != saved["step"] - plan["start_step"]
    ):
        raise ValueError("Scheduler reset position differs from global learning state")
    return plan
