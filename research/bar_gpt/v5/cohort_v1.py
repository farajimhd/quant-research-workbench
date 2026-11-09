"""One explicit v6 corpus family, with independently sealed component datasets.

The base remains immutable. A small sibling family/cohort.json references
sibling components with relative paths, so laptop UNC and workstation D: readers
resolve the same files. Training freezes all members into ONE chronological split.
"""

import json
from pathlib import Path
import re

from .shards.phase1_contract import VERSION, digest

COHORT_VERSION = "bargpt-phase1-cohort-v1"


def members(root: Path, require_all=False):
    """Return (cohort seal or None, [(name, root)]); reject paths outside the runtime family."""
    root = Path(root).resolve()
    path = root / "cohort.json"
    automatic = not path.exists()
    if automatic:
        path = root.parent / "phase1-v6-cohort" / "cohort.json"
    if not path.exists():
        return None, [("base", root)]
    wrapper = json.loads(path.read_text())
    body = wrapper["cohort"]
    if (
        wrapper["hash"] != digest(body)
        or body["version"] != COHORT_VERSION
        or body["schema"] != VERSION
    ):
        raise ValueError("Unsupported or corrupt phase-one cohort manifest")
    result, names, paths = [], set(), set()
    for item in body["members"]:
        name = item["name"]
        target = (path.parent / item["path"]).resolve()
        if automatic and not result and target != root:
            return None, [
                ("base", root)
            ]  # This family manifest belongs to another base in the same runtime parent.
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]*", name)
            or name in names
            or target in paths
            or not target.is_relative_to(path.parent.parent)
        ):
            raise ValueError("Invalid or duplicate cohort member")
        names.add(name)
        paths.add(target)
        if require_all and not (target / "contract.json").is_file():
            raise ValueError(
                f"Cohort member {name} is not published yet; wait for the queued supplement"
            )
        if require_all:
            from .quote_repair_v1 import require_complete

            require_complete(target)
            result_path = target / "result.json"
            completion = (
                json.loads(result_path.read_text()) if result_path.exists() else {}
            )
            if completion.get("state") not in {
                "quotas_reached",
                "budget_reached",
                "inventory_exhausted",
            } or completion.get("failed"):
                raise ValueError(
                    f"Cohort member {name} has not completed successfully; do not freeze a partial training corpus"
                )
        result.append((name, target))
    if not result or result[0][0] != "base" or (automatic and result[0][1] != root):
        raise ValueError("The cohort's first member must be its original base dataset")
    return wrapper["hash"], result


def published_members(root: Path):
    """Expose available members to the app, including the base while the supplement waits."""
    seal, planned = members(root)
    return seal, [
        (name, path)
        for name, path in planned
        if (path / "contract.json").is_file() and (path / "plan.json").is_file()
    ]
