"""Preserve a drained supplement across an explicit, receipt-audited producer upgrade.

Payloads are hardlinked without changing a byte. Only their new publication
receipts and checkpoint bindings change. The old component remains immutable;
the cohort switches to the replacement only after the upgrade audit is sealed.
"""

import json
from pathlib import Path

from .shards.phase1_contract import digest, write_json
from .shards.storage import verify_receipt


def upgrade(args, wrapper):
    """Verify and preserve old [N,64,18] payloads, raw tails and source cursors.

    args.upgrade_from is a stopped sibling component. The new wrapper is made
    by supplement.prepare with the identical frozen plan, authority and data
    configuration. A held OS lock prevents copying a still-running producer.
    Interrupted copies are idempotent; no already-published payload is replaced.
    """
    source = getattr(args, "upgrade_from", None)
    if source is None:
        return
    source = Path(source).resolve()
    if source.parent != args.output.parent or source in (args.output, args.base):
        raise ValueError("Upgrade source must be a distinct sibling supplement")
    old = json.loads((source / "contract.json").read_text())
    if digest(old["contract"]) != old["binding"]:
        raise ValueError("Upgrade source contract seal changed")
    audit_path = args.output / "upgrade-audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if (audit.get("old_binding"), audit.get("new_binding")) != (
            old["binding"],
            wrapper["binding"],
        ) or audit.get("audit_hash") != digest(
            {k: v for k, v in audit.items() if k != "audit_hash"}
        ):
            raise ValueError("Upgrade audit seal or producer identity changed")
        return  # Continued extraction must never be overwritten by an old checkpoint.
    for key in (
        "version",
        "cohort_id",
        "parent_binding",
        "plan_hash",
        "authority",
        "configuration",
        "format",
    ):
        if old["contract"][key] != wrapper["contract"][key]:
            raise ValueError(f"Upgrade changes a data-defining contract field: {key}")
    result = json.loads((source / "result.json").read_text())
    progress = json.loads((source / "progress.json").read_text())
    if (
        result["state"] != "stopped"
        or result.get("failed")
        or progress.get("jobs_active") != 0
    ):
        raise ValueError("Upgrade requires a cleanly drained source component")
    # The current upgrade deliberately supports the observed coarse-only run.
    # Upgrading an allocated fine pass needs an explicit allowance translation.
    phase = (
        json.loads((source / "phase.json").read_text())
        if (source / "phase.json").exists()
        else {"stage": "coarse"}
    )
    if phase["stage"] != "coarse":
        raise ValueError("Only a drained coarse-only producer can use this upgrade")
    import msvcrt

    with (source / "controller.lock").open("r+b") as owner:
        owner.seek(0)
        msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        samples, files, receipts, lineage = 0, 0, 0, []
        for receipt_path in sorted(source.glob("jobs/*/*/receipt.json")):
            previous = verify_receipt(receipt_path.parent, hashes=True)
            if previous["binding"] != old["binding"]:
                raise ValueError("Foreign receipt in upgrade source")
            target = args.output / receipt_path.parent.relative_to(source)
            target.mkdir(parents=True, exist_ok=True)
            for item in previous["files"]:
                path = target / item["name"]
                if not path.exists():
                    path.hardlink_to(receipt_path.parent / item["name"])
                files += 1
            updated = dict(previous)
            updated.update(
                binding=wrapper["binding"],
                identity=digest(
                    [
                        wrapper["binding"],
                        previous["ticker"],
                        previous["unit"],
                        next(int(f) for f, n in previous["frames"].items() if n),
                        previous["year"],
                    ]
                ),
                producer_upgrade_parent_binding=old["binding"],
                producer_upgrade_parent_receipt_hash=previous["receipt_hash"],
            )
            updated.pop("receipt_hash")
            updated["receipt_hash"] = digest(updated)
            existing = verify_receipt(target, hashes=True)
            if existing is not None and existing != updated:
                raise ValueError(
                    "Upgrade target already contains a different publication"
                )
            write_json(target / "receipt.json", updated)
            # Hardlink identity plus both payload SHA checks prove byte equality.
            verify_receipt(target, hashes=True)
            samples += previous["samples"]
            receipts += 1
            lineage.append([previous["receipt_hash"], updated["receipt_hash"]])
        if samples != result["samples"]:
            raise ValueError("Upgrade inventory does not reconcile to drained result")
        checkpoints = 0
        for path in source.glob("checkpoints/*.json"):
            checkpoint = json.loads(path.read_text())
            if checkpoint["binding"] != old["binding"]:
                raise ValueError("Foreign source cursor in upgrade")
            checkpoint["binding"] = wrapper["binding"]
            write_json(args.output / "checkpoints" / path.name, checkpoint)
            checkpoints += 1
        write_json(
            args.output / "phase.json",
            dict(stage="mixed", pass_number=0, allocated=False),
        )
        audit = dict(
            source=str(source),
            output=str(args.output),
            old_binding=old["binding"],
            new_binding=wrapper["binding"],
            samples=samples,
            files=files,
            receipts=receipts,
            checkpoints=checkpoints,
            payloads_sha256_verified=True,
            receipt_lineage_hash=digest(lineage),
            state="passed",
        )
        audit["audit_hash"] = digest(audit)
        write_json(audit_path, audit)
