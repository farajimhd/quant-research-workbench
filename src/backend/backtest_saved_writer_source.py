"""Read-only historical native proof from explicitly registered frozen sources."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
import os
from io import BytesIO
from pathlib import Path
import re
import subprocess
import sys

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SEAL = object()
_ROW_KEYS = {"root", "execution_commit", "code_hash", "backend_fingerprint",
             "published_commit", "published_fingerprint"}


@dataclass(frozen=True)
class HistoricalWriterSource:
    execution_commit: str
    code_hash: str
    backend_fingerprint: str
    native_certificate: str
    registry_hash: str
    reader_fingerprint: str
    root: str
    _seal: object


def registered_writer(context, manifest):
    """Absence selects the existing route; a configured invalid registry rejects."""
    filename = os.environ.get("BACKTEST_SAVED_WRITER_ROOTS_FILE", "")
    expected = os.environ.get("BACKTEST_SAVED_WRITER_ROOTS_SHA256", "")
    if not filename and not expected:
        return None
    if not filename or not _HEX.fullmatch(expected):
        raise ValueError("Saved writer registry requires FILE and exact SHA256")
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Saved writer registry requires an absolute regular file")
    raw = path.read_bytes()
    if len(raw) > 65536 or sha256(raw).hexdigest() != expected:
        raise ValueError("Saved writer registry bytes differ from their approval")
    registry = json.loads(raw)
    if (type(registry) is not dict or set(registry) != {"schema", "reader_fingerprint", "writers"}
            or registry["schema"] != "saved-writer-root-registry-v1"
            or not _HEX.fullmatch(str(registry["reader_fingerprint"]))
            or type(registry["writers"]) is not list or not 1 <= len(registry["writers"]) <= 16):
        raise ValueError("Saved writer registry has an unsupported closed schema")
    identities = set()
    executions = {}
    known_code_hash = False
    selected = None
    for row in registry["writers"]:
        if (type(row) is not dict or set(row) != _ROW_KEYS
                or any(type(row[k]) is not str for k in _ROW_KEYS)
                or not Path(row["root"]).is_absolute()
                or not _COMMIT.fullmatch(row["execution_commit"])
                or not _COMMIT.fullmatch(row["published_commit"])
                or any(not _HEX.fullmatch(row[k]) for k in
                       ("code_hash", "backend_fingerprint", "published_fingerprint"))
                or (row["code_hash"], row["published_commit"], row["published_fingerprint"]) in identities):
            raise ValueError("Saved writer registry has a foreign or duplicate source row")
        identities.add((row["code_hash"], row["published_commit"], row["published_fingerprint"]))
        execution = (row["root"], row["execution_commit"], row["backend_fingerprint"])
        if row["code_hash"] in executions and executions[row["code_hash"]] != execution:
            raise ValueError("Saved writer registry has ambiguous execution source roots")
        executions[row["code_hash"]] = execution
        known_code_hash |= row["code_hash"] == context.get("code_hash")
        if ((row["code_hash"], row["published_commit"], row["published_fingerprint"])
                == (context.get("code_hash"), manifest.get("approved_code_commit"),
                    manifest.get("approved_code_fingerprint"))):
            selected = row
    if selected is None:
        if known_code_hash:
            raise ValueError("Registered writer crosses its published source identity")
        return None
    return selected, expected, registry["reader_fingerprint"]


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=30)


def _git_code_hash(root, commit):
    """Original backtest_code_hash ordering and bytes, from immutable Git blobs."""
    digest = sha256()
    total = 0
    count = 0
    for directory in ("src", "research/mlops", "research/reaction_levels"):
        tree = _git(root, "ls-tree", "-r", "-l", "-z", commit, "--", directory)
        records = []
        for raw in tree.rstrip(b"\0").split(b"\0"):
            metadata, name = raw.split(b"\t", 1)
            mode, kind, oid, size = metadata.split()
            path = name.decode("utf-8")
            if path.endswith(".py"):
                if mode not in {b"100644", b"100755"} or kind != b"blob":
                    raise ValueError("Historical writer has a nonregular Git source")
                records.append((path, oid, int(size)))
        # Match sorted(Path.rglob()) in the original runtime contract, including
        # Windows path comparison semantics rather than inventing new ordering.
        records.sort(key=lambda item: Path(item[0]))
        if not records:
            raise ValueError("Historical writer Git source inventory is incomplete")
        total += sum(size for _, _, size in records)
        count += len(records)
        if total > 64 * 1024 * 1024 or count > 4096:
            raise ValueError("Historical writer Git source exceeds its bound")
        blobs = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                               input=b"".join(oid + b"\n" for _, oid, _ in records),
                               capture_output=True, check=True, timeout=30).stdout
        stream = BytesIO(blobs)
        for path, oid, size in records:
            if stream.readline() != oid + b" blob " + str(size).encode("ascii") + b"\n":
                raise ValueError("Historical writer Git blob header differs")
            data = stream.read(size)
            if len(data) != size or stream.read(1) != b"\n":
                raise ValueError("Historical writer Git blob is incomplete")
            name = path.encode("utf-8")
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(data.replace(b"\r\n", b"\n"))
        if stream.read(1):
            raise ValueError("Historical writer Git batch contains unrequested bytes")
    return digest.hexdigest()


def _verify_root(row):
    from .backtest_saved_source_authority import approved_git_source_fingerprint
    from .backtest_journal_clickhouse import backtest_code_hash
    root = Path(row["root"])
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Registered historical writer root is unavailable")
    if (_git(root, "rev-parse", "HEAD").decode("ascii").strip() != row["execution_commit"]
            or _git(root, "status", "--porcelain", "--untracked-files=all").strip()):
        raise ValueError("Registered historical writer must have its exact clean HEAD")
    ignored = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--",
                   ":(glob)*.py", ":(glob)src/**/*.py", ":(glob)research/**/*.py",
                   ":(glob)pipelines/**/*.py", ":(glob)scripts/**/*.py")
    if ignored.strip():
        raise ValueError("Historical writer has ignored Python import candidates")
    if (_git_code_hash(root, row["execution_commit"]) != row["code_hash"]
            or backtest_code_hash(root) != row["code_hash"]
            or approved_git_source_fingerprint(str(root), row["execution_commit"]) != row["backend_fingerprint"]):
        raise ValueError("Historical writer Git and runtime source identities differ")


_PROOF_PROGRAM = r'''
import os,sys,json,subprocess
from pathlib import Path
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
root=Path(sys.argv[1]);number=int(sys.argv[2]);sys.path.insert(0,str(root))
from src.backend.historical_runtime_versions import backend_source_fingerprint
from src.backend.backtest_journal_clickhouse import backtest_code_hash
from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
before=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
proof=certify_numbered_fixed_v4_projection(number)
assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=all']).strip()
assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==before
print(json.dumps({'schema':'historical-native-writer-source-v1','number':number,'commit':before,
 'backend_fingerprint':backend_source_fingerprint(),'code_hash':backtest_code_hash(root),'certificate':proof}))
'''


@lru_cache(maxsize=16)
def _native_proof(root, commit, code_hash, backend, number, registry_hash):
    # No credential environment or caller-controlled Python import path enters
    # the historical source-only process. -I also disables user-site imports.
    environment = {k: v for k, v in os.environ.items() if k.upper() in
                   {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}}
    # Preserve only the resolved OS home identity required by fixed catalog roots.
    environment["USERPROFILE" if os.name == "nt" else "HOME"] = str(Path.home())
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", _PROOF_PROGRAM, root, str(number)],
                            env=environment, capture_output=True, timeout=180, check=False)
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError("Historical native writer source certification failed")
    payload = json.loads(result.stdout)
    if (type(payload) is not dict or set(payload) !=
            {"schema", "number", "commit", "backend_fingerprint", "code_hash", "certificate"}
            or payload["schema"] != "historical-native-writer-source-v1"
            or type(payload["number"]) is not int or payload["number"] != number
            or payload["commit"] != commit or payload["code_hash"] != code_hash
            or payload["backend_fingerprint"] != backend
            or not _HEX.fullmatch(str(payload["certificate"]))):
        raise ValueError("Historical native writer proof crosses its source binding")
    return payload["certificate"]


def certify_historical_writer(context, revision):
    strategy = revision["payload"]["strategy"]
    selection = registered_writer(context, strategy["numbered_release"])
    if selection is None:
        return None
    row, registry_hash, reader_fingerprint = selection
    _verify_root(row)
    certificate = _native_proof(row["root"], row["execution_commit"], row["code_hash"],
                                row["backend_fingerprint"], strategy["strategy_number"], registry_hash)
    _verify_root(row)
    return HistoricalWriterSource(row["execution_commit"], row["code_hash"], row["backend_fingerprint"],
                                  certificate, registry_hash, reader_fingerprint, row["root"], _SEAL)
