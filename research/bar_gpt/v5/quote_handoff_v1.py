"""Prepare an exact-membership split for a completed quote-only migration.

This module changes metadata, never model tensors or running processes. Inputs
are the original sealed chronological split and the released repaired cohort.
Every selected file is reconciled with its repaired receipt. Row ranges, order,
clocks, timeframes and the original full-context overlap purge stay identical.
The resulting certificate is preparation evidence, NOT permission to bypass
checkpoint compatibility or a claim that training has switched datasets.
"""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from .data_v1 import dataset_identity, load_split
from .quote_repair_v1 import require_complete, verify_seal
from .receipt_index_v1 import index_receipt
from .shards.phase1_contract import digest, write_json


def membership_hash(manifest, roots):
    """Hash ordered sample identities independently of mutable quote payloads.

    ``manifest`` has train/validation lists of sealed file ranges. ``roots`` maps
    stable member names to absolute paths. Relative file identity plus start/stop
    determines every selected [64,18] record; membership order is preserved.
    """
    rows = {}
    for split, entries in manifest["splits"].items():
        rows[split] = []
        for item in entries:
            path = Path(item["path"])
            matches = [
                (name, path.relative_to(root).as_posix())
                for name, root in roots.items()
                if path.is_relative_to(root)
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Selected file must belong to exactly one cohort member"
                )
            rows[split].append([*matches[0], item["start"], item["stop"]])
    return digest(rows)


def rebind_split(original, identity, dataset, resolve_entry):
    """Replace only paths, quote hashes and receipt seals in an immutable split.

    ``resolve_entry(old_entry)`` supplies a complete repaired receipt-index entry.
    Everything except path/SHA/receipt seal must match before selected row bounds
    are restored. This pure operation does not read payloads or launch a loader.
    """
    result = deepcopy(original)
    result.pop("manifest_hash", None)
    result.pop("loader_index", None)  # Derived mmap indices bind the OLD split hash.
    for name, entries in original["splits"].items():
        updated = []
        for old in entries:
            revised = dict(resolve_entry(old))
            new = dict(revised)
            for field in ("path", "sha256", "receipt_hash", "start", "stop"):
                new.pop(field, None)
            expected = {
                key: value
                for key, value in old.items()
                if key not in {"path", "sha256", "receipt_hash", "start", "stop"}
            }
            if new != expected or not 0 <= old["start"] < old["stop"] <= old["rows"]:
                raise ValueError(
                    "Quote repair changed sample geometry or split metadata"
                )
            revised.update(start=old["start"], stop=old["stop"])
            updated.append(revised)
        result["splits"][name] = updated
    result.update(identity, dataset=str(dataset))
    result["manifest_hash"] = digest(result)
    return result


def rebind_calibration(original, certificate, source_split, target_split):
    """Retain the encoder's existing activity units during quote-data migration.

    Inputs are JSON dictionaries. ``original`` contains the historical
    training-only median/scale vectors. This does NOT refit or describe them as
    corrected-data statistics: changing these units would also change the inputs
    seen by the already-trained weights. Provenance records the old split and
    selection hash explicitly. The corrected-data loader still applies repaired
    validity masks. Checkpoint compatibility must continue to require exactly
    these same vectors, as well as the new split identity.
    """
    import math

    verify_seal(certificate, "hash")
    if (
        certificate.get("state") != "prepared-not-activated"
        or original.get("split_hash") != source_split
        or certificate["source_split"] != source_split
        or certificate["target_split"] != target_split
        or source_split == target_split
    ):
        raise ValueError(
            "Calibration migration requires the exact sealed split transition"
        )
    median, scale = original["median"], original["scale"]
    if (
        len(median) != 6
        or len(scale) != 6
        or not all(math.isfinite(value) for value in [*median, *scale])
        or not all(value > 0 for value in scale)
    ):
        raise ValueError("Invalid frozen activity normalization vectors")
    if "quote_migration" in original:
        raise ValueError("Do not silently chain different calibration migrations")
    result = deepcopy(original)
    result["split_hash"] = target_split
    result["quote_migration"] = dict(
        certificate=certificate["hash"],
        source_calibration=digest(original),
        source_split=source_split,
        source_selection_hash=original["selection_hash"],
        policy="frozen-original-training-only-units; no corrected-data refit",
    )
    return result


def prepare_split(old_path, repaired_dataset, output):
    """Publish a prepared split and certificate after both repair releases exist.

    Paths are ``Path`` instances. All payloads were scanned by the repair, and
    their repaired receipts are checked here without repeating full payload I/O.
    Actual loader access must still verify each payload SHA/header and records.
    Calibration and checkpoint migration are separate, explicit future steps.
    """
    original = load_split(old_path)
    identity = dataset_identity(repaired_dataset)
    old_members = {item["name"]: item for item in original["dataset_members"]}
    new_members = {item["name"]: item for item in identity["dataset_members"]}
    if old_members.keys() != new_members.keys():
        raise ValueError("Quote repair changed cohort membership")
    roots = {name: Path(item["path"]) for name, item in old_members.items()}
    targets = {name: Path(item["path"]) for name, item in new_members.items()}
    releases = {}
    for name, target in targets.items():
        if target == roots[name]:
            raise ValueError("A quote migration must use separate repaired roots")
        require_complete(target)
        release = json.loads((target / "repair-result.json").read_text())
        wrapper = json.loads((target / "contract.json").read_text())
        if wrapper["contract"]["repair_parent_binding"] != old_members[name]["binding"]:
            raise ValueError("Repaired cohort is not descended from this training data")
        releases[name] = release["hash"]

    @lru_cache(maxsize=256)
    def receipt_entries(path, binding):
        """Reuse a bounded number of receipt reads across fragmented split ranges."""
        body = json.loads(path.read_text())
        verify_seal(body, "receipt_hash")
        entries = {
            Path(item["path"]).name: item for item in index_receipt(path, binding)
        }
        return body["quote_repair"]["parent_receipt"], entries

    def resolve(old):
        matches = [
            name
            for name, root in roots.items()
            if Path(old["path"]).is_relative_to(root)
        ]
        if len(matches) != 1:
            raise ValueError("Ambiguous source member in old split")
        name = matches[0]
        relative = Path(old["path"]).relative_to(roots[name])
        receipt = targets[name] / relative.parent / "receipt.json"
        parent, entries = receipt_entries(receipt, new_members[name]["binding"])
        if parent != old["receipt_hash"]:
            raise ValueError("Repaired receipt has a different source ancestry")
        return entries[relative.name]

    prepared = rebind_split(original, identity, repaired_dataset, resolve)
    before = membership_hash(original, roots)
    after = membership_hash(prepared, targets)
    if before != after:
        raise ValueError("Training/validation sample membership or order changed")
    certificate = dict(
        version="bargpt-quote-split-handoff-v1",
        state="prepared-not-activated",
        source_split=original["manifest_hash"],
        target_split=prepared["manifest_hash"],
        membership_hash=before,
        repair_releases=releases,
        counts=original["counts"],
        cutoff_us=original["cutoff_us"],
        purged_crossing=original["purged_crossing"],
        remaining="audit calibration and checkpoint transition before trainer activation",
    )
    certificate["hash"] = digest(certificate)
    if output.exists() or output.with_suffix(".certificate.json").exists():
        raise FileExistsError("Preserve existing handoff evidence; choose a new output")
    write_json(output, prepared)
    write_json(output.with_suffix(".certificate.json"), certificate)
    return certificate
