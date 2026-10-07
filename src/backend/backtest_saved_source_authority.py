"""Read-only saved-source proof; never an execution or resume approval.

Historical Git objects are hashed, not imported or executed. The installed
reader must separately pass its current reviewed projection certificate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import re
import subprocess
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.journal_contract import canonical_json

_SEAL = object()
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_TREE_BYTES = 1024 * 1024
_PREFIXES = ("src/backend/", "src/trading_runtime/")


@lru_cache(maxsize=16)
def approved_git_source_fingerprint(root: str, commit: str) -> str:
    """Hash a bounded immutable tracked source tree with the runtime algorithm."""
    if re.fullmatch(r"[0-9a-f]{40}", commit or "") is None:
        raise ValueError("Saved source requires an exact approved Git commit")
    command = ["git", "-C", root]
    try:
        tree = subprocess.run(
            [*command, "ls-tree", "-r", "-l", "-z", "--full-tree", commit,
             "--", "src/backend", "src/trading_runtime"],
            check=True, capture_output=True, timeout=30,
        ).stdout
        if not tree or len(tree) > _MAX_TREE_BYTES:
            raise ValueError("Saved source inventory is empty or exceeds its bound")
        expected = {}
        total = 0
        for record in tree.rstrip(b"\0").split(b"\0"):
            metadata, raw_name = record.split(b"\t", 1)
            mode, kind, _object_id, raw_size = metadata.split()
            name = raw_name.decode("utf-8")
            if (mode not in {b"100644", b"100755"} or kind != b"blob"
                    or not name.startswith(_PREFIXES)
                    or ".." in PurePosixPath(name).parts):
                raise ValueError("Saved source has an unsupported tracked file")
            size = int(raw_size)
            if size < 0:
                raise ValueError("Saved source has an invalid file size")
            total += size
            if total > _MAX_SOURCE_BYTES:
                raise ValueError("Saved source exceeds its byte bound")
            if name.endswith(".py"):
                if name in expected:
                    raise ValueError("Saved source inventory has duplicate paths")
                expected[name] = (size, _object_id)
        if not expected or len(expected) > 4096:
            raise ValueError("Saved source Python inventory exceeds its bounds")
        # Read raw immutable blobs. `git archive` applies checkout attributes
        # (including CRLF conversion), so archive sizes are not blob sizes.
        ordered = sorted(expected)
        blobs = subprocess.run(
            [*command, "cat-file", "--batch"],
            input=b"".join(expected[name][1] + b"\n" for name in ordered),
            check=True, capture_output=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Approved saved source is unavailable in local Git objects") from exc
    if len(blobs) > _MAX_SOURCE_BYTES + len(expected) * 100:
        raise ValueError("Saved source blobs exceed their inventory bound")
    digest = sha256()
    with BytesIO(blobs) as stream:
        for name in ordered:
            size, object_id = expected[name]
            if stream.readline() != object_id + b" blob " + str(size).encode() + b"\n":
                raise ValueError("Saved source blob header differs from its tracked inventory")
            raw = stream.read(size)
            if len(raw) != size or stream.read(1) != b"\n":
                raise ValueError("Saved source blob bytes are incomplete")
            data = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            digest.update(name.encode("utf-8") + b"\n")
            digest.update(data.encode("utf-8") + b"\0")
        if stream.read(1):
            raise ValueError("Saved source blob read returned unrequested bytes")
    return digest.hexdigest()


@dataclass(frozen=True)
class SavedReviewSourceAuthority:
    run_id: str
    configuration_hash: str
    strategy_number: int
    approved_commit: str
    approved_fingerprint: str
    reader_fingerprint: str
    reader_certificate: str
    _seal: Any = field(repr=False, compare=False)
    historical_writer: Any = field(default=None, repr=False)
    execution_code_hash: str = ""

    def evidence(self) -> dict[str, Any]:
        evidence = {
            "scope": "saved_run_read_only_no_execution_or_resume",
            "run_id": self.run_id, "configuration_hash": self.configuration_hash,
            "strategy_number": self.strategy_number,
            "approved_code_commit": self.approved_commit,
            "approved_code_fingerprint": self.approved_fingerprint,
            "reader_source_fingerprint": self.reader_fingerprint,
            "reader_projection_certificate": self.reader_certificate,
        }
        if self.historical_writer is not None:
            evidence["historical_writer"] = {
                "execution_commit": self.historical_writer.execution_commit,
                "execution_code_hash": self.historical_writer.code_hash,
                "execution_backend_fingerprint": self.historical_writer.backend_fingerprint,
                "native_projection_certificate": self.historical_writer.native_certificate,
                "root_registry_sha256": self.historical_writer.registry_hash,
            }
        return evidence


def certify_saved_review_source(
    context: Mapping[str, Any], revision: Mapping[str, Any],
) -> SavedReviewSourceAuthority:
    """Bind immutable approved historical bytes to an independently sealed reader."""
    from src.backend import historical_runtime_versions as versions
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from src.backend.backtest_strategy_one_configuration import is_numbered_fixed_configuration
    from src.trading_runtime.numbered_fixed_strategy import resolve_numbered_fixed_strategy

    payload = dict(revision.get("payload") or {})
    strategy = dict(payload.get("strategy") or {})
    number = strategy.get("strategy_number")
    run_id = str(UUID(str(context.get("run_id"))))
    if (context.get("mode") != "backtest" or context.get("evaluation_interval_ms") != 100
            or type(number) is not int or number < 20
            or context.get("strategy_revision") != number
            or context.get("strategy_id") != strategy.get("strategy_id")
            or context.get("configuration_hash") != revision.get("content_hash")
            or sha256(canonical_json(payload).encode("utf-8")).hexdigest() != revision.get("content_hash")
            or re.fullmatch(r"[0-9a-f]{64}", str(revision.get("content_hash") or "")) is None
            or not is_numbered_fixed_configuration(payload)):
        raise ValueError("Saved review source differs from its immutable run configuration")
    contract = resolve_numbered_fixed_strategy(strategy["strategy_id"], number)
    selected = [(row.get("strategy_id"), row.get("strategy_revision"))
                for row in payload.get("assignments") or []
                if row.get("status") not in {"disabled", "completed", "error"}]
    if any(identity != (contract.strategy_id, number) for identity in selected):
        raise ValueError("Saved review assignments differ from their installed release")
    manifest = dict(strategy.get("numbered_release") or {})
    commit = manifest.get("approved_code_commit", "")
    approved = manifest.get("approved_code_fingerprint", "")
    if (re.fullmatch(r"[0-9a-f]{64}", approved) is None
            or approved_git_source_fingerprint(str(versions.ROOT), commit) != approved):
        raise ValueError("Saved approved Git source differs from its published fingerprint")
    current = versions.backend_source_fingerprint()
    if current != versions.LOADED_BACKEND_FINGERPRINT:
        raise RuntimeError("Saved review reader source changed after startup; restart the backend")
    from .backtest_saved_writer_source import certify_historical_writer
    writer = certify_historical_writer(context, revision)
    if writer is None:
        # This is explicit source-identity routing, never an exception fallback.
        certificate = versions._loaded_numbered_projection(
            certify_numbered_fixed_v4_projection, current, number)
    else:
        from .backtest_saved_reader_certification import certify_saved_reader_source
        certificate = certify_saved_reader_source(writer.reader_fingerprint)
    if re.fullmatch(r"[0-9a-f]{64}", certificate or "") is None:
        raise ValueError("Saved review reader lacks its current projection certificate")
    return SavedReviewSourceAuthority(
        run_id, revision["content_hash"], number, commit, approved,
        current, certificate, _SEAL, writer, str(context.get("code_hash") or ""))


def validate_saved_review_source(
    authority: SavedReviewSourceAuthority, revision: Mapping[str, Any],
    *, run_id: str | None = None,
) -> None:
    """Recheck reader freshness and exact binding at each internal read boundary."""
    from src.backend import historical_runtime_versions as versions
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    if type(authority) is not SavedReviewSourceAuthority or authority._seal is not _SEAL:
        raise ValueError("Saved review requires an internally certified read-only source")
    strategy = dict(dict(revision.get("payload") or {}).get("strategy") or {})
    manifest = dict(strategy.get("numbered_release") or {})
    if (authority.configuration_hash != revision.get("content_hash")
            or sha256(canonical_json(revision.get("payload")).encode("utf-8")).hexdigest() != authority.configuration_hash
            or authority.strategy_number != strategy.get("strategy_number")
            or authority.approved_commit != manifest.get("approved_code_commit")
            or authority.approved_fingerprint != manifest.get("approved_code_fingerprint")
            or run_id is not None and authority.run_id != run_id
            or authority.reader_fingerprint != versions.LOADED_BACKEND_FINGERPRINT
            or authority.reader_fingerprint != versions.backend_source_fingerprint()):
        raise ValueError("Saved review source binding or reader freshness changed")
    if authority.historical_writer is None:
        certificate = versions._loaded_numbered_projection(
            certify_numbered_fixed_v4_projection, authority.reader_fingerprint,
            authority.strategy_number)
    else:
        from .backtest_saved_writer_source import certify_historical_writer
        from .backtest_saved_reader_certification import certify_saved_reader_source
        writer = certify_historical_writer({"code_hash": authority.execution_code_hash}, revision)
        if writer is None or writer != authority.historical_writer:
            raise ValueError("Saved review historical writer binding changed")
        certificate = certify_saved_reader_source(writer.reader_fingerprint)
    if (approved_git_source_fingerprint(str(versions.ROOT), authority.approved_commit)
            != authority.approved_fingerprint or certificate != authority.reader_certificate):
        raise ValueError("Saved review source or reader certificate changed")


def saved_review_runtime_version_check(
    authority: SavedReviewSourceAuthority, revision: Mapping[str, Any],
) -> dict[str, Any]:
    validate_saved_review_source(authority, revision)
    return {
        "id": "saved_review_source", "label": "Approved historical source and current reader",
        "required": True, "status": "ready",
        "summary": "Historical source bytes and current read-only projection are certified.",
        "evidence": authority.evidence(),
    }
