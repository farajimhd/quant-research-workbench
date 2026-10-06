"""Prepared canonical archive consolidation; no installed/read or financial authority.

Books are checked in their original complete chronology through the requested
prior-session fence. Dated membership never implies coverage of omitted dates.
The transport seam below is deliberately not a ClickHouse or grant installer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import gzip
from io import BytesIO
from pathlib import Path
import re
from typing import Protocol
from zoneinfo import ZoneInfo

from src.backend.backtest_declared_ladder_seed import _previous_session
from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.filtered_v7_history import successor as _legacy_successor
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.level_book_store import read, verified_book
from src.market_engine.v7_catalog import CAMPAIGNS
from src.trading_runtime.arte_journal_schema import TableContract
from .campaign_source import REPORTING_REVISION, source_hash

from src.market_engine.canonical_v7_archive_contract import (
    VERSION, MEMBER_TABLE, COMMIT_TABLE, MEMBER_COLUMNS, COMMIT_COLUMNS,
    commit_row, validate_commit, inventory_hash, scope_hash, SOURCE_SCOPE_KEYS,
)
PROVENANCE = TableContract(MEMBER_TABLE, MEMBER_COLUMNS,
    'toYYYYMM(target_session)', 'consolidation_hash, ticker, target_session')
COMMIT = TableContract(COMMIT_TABLE, COMMIT_COLUMNS, 'tuple()', 'consolidation_hash')
TABLES = (PROVENANCE, COMMIT)


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None or value == '0' * 64:
        raise ValueError('Archive identity must be nonzero lowercase SHA256')
    return value


def _day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Archive session must be canonical ISO date')
    return date.fromisoformat(value)


def _equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if type(left) in (list, tuple):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _file_hash(path):
    with path.open('rb') as stream:
        value = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def successor(root, parent, ticker):
    from .canonical_metadata_parent import VERSION as PARENT_CONTRACT, successor as metadata_successor
    if parent.get('version') == PARENT_CONTRACT:
        return metadata_successor(root,parent,ticker)
    return _legacy_successor(root,parent,ticker)


@dataclass(frozen=True)
class ArchiveRequest:
    """Upstream scope references, not self-issued market certification."""
    target_session: str
    ticker: str
    parent_relative: str
    original_market_token: str
    scoped_market_token: str
    original_price_token: str
    scoped_price_token: str
    exclusion_policy_hash: str
    metadata_parent_hash: str = ''

    def __post_init__(self):
        _day(self.target_session)
        if type(self.ticker) is not str or re.fullmatch(r'[A-Z0-9.\- ]{1,30}', self.ticker) is None:
            raise ValueError('Invalid archive ticker identity')
        from .canonical_metadata_parent import is_relative, relative, catalog_relative
        parent_relative = catalog_relative(self.parent_relative)
        if parent_relative in CAMPAIGNS:
            if type(self.metadata_parent_hash) is not str or self.metadata_parent_hash != '':
                raise ValueError('Legacy archive request cannot claim metadata parent authority')
            object.__setattr__(self, 'parent_relative', parent_relative)
        elif is_relative(self.parent_relative):
            _hash(self.metadata_parent_hash)
            if self.parent_relative != relative(self.metadata_parent_hash):
                raise ValueError('Canonical archive parent path/hash differs')
        else:
            raise ValueError('Archive parent is outside explicit catalog or metadata authority')
        for value in (self.original_market_token, self.scoped_market_token,
                      self.original_price_token, self.scoped_price_token, self.exclusion_policy_hash):
            _hash(value)

    @property
    def scope_hash(self):
        return scope_hash({name:getattr(self,name) for name in SOURCE_SCOPE_KEYS})


@dataclass(frozen=True)
class ArchiveMember:
    request: ArchiveRequest
    seed_session: str
    available_at: str
    parent_plan_hash: str
    successor_plan_hash: str
    source_plan_content_hash: str
    checkpoint_hash: str
    seed_content_hash: str
    receipt_hash: str
    book_hash: str
    chronology_hash: str
    chronology_sessions: int

    def __post_init__(self):
        if type(self.request) is not ArchiveRequest:
            raise ValueError('Exact archive request required')
        self.request.__post_init__()
        if _day(self.seed_session) != _previous_session(_day(self.request.target_session)):
            raise ValueError('Archive seed is not exact previous NYSE session')
        if type(self.available_at) is not str:
            raise ValueError('Archive availability requires exact string')
        stamp = datetime.fromisoformat(self.available_at)
        cutoff = datetime.combine(_day(self.request.target_session), time(4), ZoneInfo('America/New_York'))
        if stamp.tzinfo is None or stamp.astimezone(timezone.utc) > cutoff.astimezone(timezone.utc):
            raise ValueError('Archive availability exceeds target 04ET fence')
        if type(self.chronology_sessions) is not int or self.chronology_sessions <= 0:
            raise ValueError('Archive chronology count must be positive integer')
        for name in ('parent_plan_hash','successor_plan_hash','source_plan_content_hash',
                     'checkpoint_hash','seed_content_hash','receipt_hash','book_hash','chronology_hash'):
            _hash(getattr(self, name))


class _ArchiveSnapshot:
    """One planning operation/ticker only; no global cache or approval seam."""
    def __init__(self):
        self.metadata={};self.books={};self.hashes={}

    def _load(self,path):
        path=Path(path)
        raw=path.read_bytes()
        if len(raw)>128*1024*1024:
            raise ValueError('Archive artifact exceeds bounded unit size')
        self.hashes[path]=sha256(raw).hexdigest()
        if path.suffix=='.gz':
            with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
                decoded=stream.read(128*1024*1024+1)
        else:
            decoded=raw
        if len(decoded)>128*1024*1024:
            raise ValueError('Expanded archive artifact exceeds bounded unit size')
        return json.loads(decoded)

    def read(self,path):
        path=Path(path)
        if path not in self.metadata:self.metadata[path]=self._load(path)
        return self.metadata[path]

    def book(self,path):
        path=Path(path)
        if path not in self.books:
            value=self._load(path)
            if value.get('checkpoint_hash')!=digest({k:v for k,v in value.items() if k!='checkpoint_hash'}):
                raise ValueError('Level book checkpoint hash mismatch')
            # Only verified metadata retained between requested dates. Compaction
            # later reloads original complete books and validates their inventory.
            keys=('ticker','session','available_at','prior_checkpoint_hash','input_hash','input_policy','checkpoint_hash','version','retrospective','source_extraction_version','band_config')
            self.books[path]={k:value.get(k) for k in keys}
        return self.books[path]

    def file_hash(self,path):
        path=Path(path)
        if path not in self.hashes:self._load(path)
        return self.hashes[path]


def verify_archive_member(root: Path, request: ArchiveRequest) -> ArchiveMember:
    return _verify_archive_member(root,request,_ArchiveSnapshot())

def _verify_archive_member(root: Path, request: ArchiveRequest, snapshot: _ArchiveSnapshot) -> ArchiveMember:
    """Re-read producer artifacts; neither supplied hashes nor ready flags approve them."""
    if type(request) is not ArchiveRequest:
        raise ValueError('Exact archive request required')
    request.__post_init__()
    root = Path(root).resolve()
    parent = snapshot.read(root / request.parent_relative / 'plan.json')
    if parent.get('plan_hash') != digest({k:v for k,v in parent.items() if k != 'plan_hash'}):
        raise ValueError('Parent archive plan hash mismatch')
    from .campaign import VERSION as PARENT_VERSION
    from .canonical_metadata_parent import VERSION as METADATA_VERSION, load_parent
    from src.market_engine.streaming_level_book import EXTRACTION_VERSION
    from src.market_engine.reaction_band import CONFIG
    from src.backend.swing_book_source import HISTORICAL_POLICY, session_bounds
    if request.metadata_parent_hash:
        checked=load_parent(root,root/request.parent_relative/'plan.json',request.metadata_parent_hash)
        if not _equal(parent,checked) or parent.get('version') != METADATA_VERSION:
            raise ValueError('Canonical archive metadata parent differs')
        scopes=[s for s in parent['scopes'] if s['target_session']==request.target_session]
        if (len(scopes)!=1 or request.ticker not in scopes[0]['tickers']
                or any(getattr(request,k)!=scopes[0][k] for k in SOURCE_SCOPE_KEYS)):
            raise ValueError('Canonical archive request differs from parent dated source scope')
    if (parent.get('version') != (METADATA_VERSION if request.metadata_parent_hash else PARENT_VERSION)
            or parent.get('extraction_version') != EXTRACTION_VERSION
            or not _equal(parent.get('band_config'), CONFIG) or parent.get('source_policy') != HISTORICAL_POLICY):
        raise ValueError('Parent producer contract differs')
    matches = [row for row in parent['rows'] if row['ticker'] == request.ticker]
    if len(matches) != 1 or matches[0].get('status') == 'deferred':
        raise ValueError('Archive parent has missing/ambiguous/deferred ticker')
    output, expected = successor(root, parent, request.ticker)
    plan = snapshot.read(output / 'plan.json')
    if not _equal(plan, expected) or plan.get('input_policy') != POLICY or plan.get('reporting_revision') != REPORTING_REVISION:
        raise ValueError('Canonical successor differs from current producer/kernel contract')
    directory = matches[0]['directory']
    if type(directory) is not str or Path(directory).name != directory or directory in ('.','..'):
        raise ValueError('Unsafe archive ticker directory')
    target = output / 'tickers' / directory
    source = snapshot.read(target / 'source-plan.json')
    if source.get('plan_hash') != plan['plan_hash']:
        raise ValueError('Archive source plan differs from successor')
    _hash(source.get('reporting_coverage_hash'))
    days = source.get('days')
    if type(days) is not list or not days:
        raise ValueError('Archive source chronology missing')
    names = [row.get('source_date') for row in days]
    if names != sorted(set(names)) or any(row.get('ticker') != request.ticker for row in days):
        raise ValueError('Archive source chronology is duplicated/foreign')
    prior = _previous_session(_day(request.target_session)).isoformat()
    prefix = [row for row in days if _day(row['source_date']) <= _day(prior)]
    if not prefix or prefix[-1]['source_date'] != prior:
        raise ValueError('Archive omits exact prior NYSE source session')
    ready_path = target / 'ready.json'
    if ready_path.exists():
        ready = snapshot.read(ready_path)
        if ready.get('plan_hash') != plan['plan_hash'] or ready.get('source_plan_hash') != digest(source):
            raise ValueError('Archive ready source identity mismatch')
    else:
        marker_path = target / 'prefixes' / (request.target_session + '.json')
        marker = snapshot.read(marker_path)
        expected_marker = dict(version=1,plan_hash=plan['plan_hash'],ticker=request.ticker,
            before=request.target_session,sessions=len(prefix),through=prior,source_plan_hash=digest(source))
        if marker.get('prefix_hash') != digest({k:v for k,v in marker.items() if k != 'prefix_hash'}) or any(not _equal(marker.get(k),v) for k,v in expected_marker.items()):
            raise ValueError('Archive prefix publication mismatch')
    checkpoint = None
    latest_book_path = None
    chronology = []
    current_book_hash = None
    for metadata in prefix:
        day = metadata['source_date'];receipt_path = target / 'receipts' / (day + '.json')
        receipt = snapshot.read(receipt_path)
        if receipt.get('source_hash') != source_hash(metadata,plan['rules']) or receipt.get('parent_hash') != checkpoint:
            raise ValueError('Archive receipt source/parent chain mismatch')
        if receipt.get('state') == 'complete':
            path = target / 'books' / (day + '.json.gz');book = snapshot.book(path)
            genesis = digest(dict(ticker=request.ticker,session='0001-01-01',
                available_at=session_bounds(day)[0].timestamp(),levels=[],
                source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG))
            if (book.get('ticker') != request.ticker or book.get('session') != day
                    or book.get('input_policy') != POLICY or book.get('prior_checkpoint_hash') != (checkpoint if checkpoint is not None else genesis)
                    or book.get('version') != 'historical-level-mle-book-1'
                    or book.get('retrospective') is not True
                    or book.get('source_extraction_version') != EXTRACTION_VERSION
                    or not _equal(book.get('band_config'),CONFIG)
                    or book.get('input_hash') != receipt.get('bar_hash')
                    or receipt.get('checkpoint_hash') != book.get('checkpoint_hash')):
                raise ValueError('Archive book identity/policy/parent mismatch')
            stamp = book.get('available_at')
            if type(stamp) not in (int,float) or type(stamp) is bool:
                raise ValueError('Archive book availability scalar invalid')
            from src.backend.swing_book_source import session_bounds
            if stamp != session_bounds(day)[1].timestamp():
                raise ValueError('Archive book availability has foreign source day')
            checkpoint = _hash(book['checkpoint_hash']);current_book_hash = snapshot.file_hash(path)
            latest_book_path = path
        elif receipt.get('state') != 'empty':
            raise ValueError('Archive receipt is not completed or observed empty')
        chronology.append((day,snapshot.file_hash(receipt_path),checkpoint,current_book_hash))
    _hash(checkpoint)
    # Empty dates retain the genuine preceding checkpoint, never fabricate a book.
    if current_book_hash is None:
        raise ValueError('Archive empty prefix has no valid checkpoint')
    from src.backend.swing_book_source import session_bounds
    _, end = session_bounds(prior)
    projected = projected_archive_seed(verified_book(latest_book_path), seed_session=prior,
        available_at=end.astimezone(timezone.utc).isoformat(), source_plan_hash=plan['plan_hash'])
    return ArchiveMember(request,prior,end.astimezone(timezone.utc).isoformat(),_hash(parent['plan_hash']),
        _hash(plan['plan_hash']),digest(source),checkpoint,projected['checkpoint_hash'],snapshot.file_hash(receipt_path),current_book_hash,
        digest(chronology),len(prefix))


def projected_archive_seed(book: dict, *, seed_session: str, available_at: str,
                           source_plan_hash: str) -> dict:
    """Current-state snapshot projection only; never publish its synthetic intervals.

    Published intervals must be compacted from full genuine chronology. The seed
    hash pins the SAME pure decoder/content used by single and batched SQL reads.
    """
    from .clickhouse_persistence import compact_checkpoints, datetime64_ns, epoch_ns
    from src.backend.structural_v7_seed import _assemble_seed
    intervals,observations,coverage=compact_checkpoints((book,),source_plan_hash)
    pinned=dict(coverage[0],session_date=seed_session,
        available_at=datetime64_ns(epoch_ns(str(datetime.fromisoformat(available_at).timestamp()))))
    levels=sorted(intervals,key=lambda row:row['level_id'])
    observations=sorted(observations,key=lambda row:(row['level_id'],row['observation_id']))
    return _assemble_seed(str(book['ticker']),_day(seed_session),pinned,levels,observations)


def projection_source_identity():
    repo=Path(__file__).resolve().parents[3]
    paths=('src/backend/structural_v7_seed.py','research/level_book/v7/clickhouse_persistence.py',
           'src/market_engine/canonical_v7_archive_contract.py','research/level_book/v7/canonical_archive_publication.py',
           'research/level_book/v7/canonical_metadata_parent.py')
    return tuple((name,_file_hash(repo/name)) for name in paths)

@dataclass(frozen=True)
class CanonicalArchivePlan:
    members: tuple[ArchiveMember, ...]
    projection_sources: tuple[tuple[str,str], ...]
    version: str = VERSION

    def __post_init__(self):
        if type(self.version) is not str or self.version != VERSION or type(self.members) is not tuple or not self.members:
            raise ValueError('Invalid canonical archive plan schema')
        if type(self.projection_sources) is not tuple or not _equal(self.projection_sources,projection_source_identity()):
            raise ValueError('Archive projection/decoder source identity changed')
        keys = []
        for member in self.members:
            if type(member) is not ArchiveMember:
                raise ValueError('Invalid canonical archive member type')
            member.__post_init__();keys.append((member.request.ticker,member.request.target_session))
        if keys != sorted(set(keys)):
            raise ValueError('Archive membership must be unique and canonically ordered')
        for ticker in {key[0] for key in keys}:
            group = [m for m in self.members if m.request.ticker == ticker]
            if len({(m.parent_plan_hash,m.successor_plan_hash,m.source_plan_content_hash) for m in group}) != 1:
                raise ValueError('Mixed producer lineage for one ticker')

    def payload(self):
        return asdict(self)

    @property
    def token(self):
        return digest(self.payload())


def prepare_archive_plan(root: Path, requests: tuple[ArchiveRequest, ...]) -> CanonicalArchivePlan:
    if type(requests) is not tuple or not requests:
        raise ValueError('Nonempty exact archive requests required')
    keys = [(r.ticker,r.target_session) for r in requests if type(r) is ArchiveRequest]
    if len(keys) != len(requests) or keys != sorted(set(keys)):
        raise ValueError('Duplicate/foreign/unsorted archive requests')
    members=[];previous_ticker=None;snapshot=None
    for request in requests:
        if request.ticker!=previous_ticker:
            snapshot=_ArchiveSnapshot();previous_ticker=request.ticker
        members.append(_verify_archive_member(root,request,snapshot))
    return CanonicalArchivePlan(tuple(members),projection_source_identity())


def member_rows(plan: CanonicalArchivePlan):
    if type(plan) is not CanonicalArchivePlan:
        raise ValueError('Exact canonical archive plan required')
    plan.__post_init__();result=[]
    for member in plan.members:
        row={key:getattr(member,key) for key in ('seed_session','available_at','parent_plan_hash','successor_plan_hash',
            'source_plan_content_hash','checkpoint_hash','seed_content_hash','receipt_hash','book_hash','chronology_hash')}
        row.update(consolidation_hash=plan.token,ticker=member.request.ticker,
                   target_session=member.request.target_session,input_policy=POLICY,
                   reporting_revision=REPORTING_REVISION,scope_hash=member.request.scope_hash)
        stamp = datetime.fromisoformat(row['available_at']).astimezone(timezone.utc)
        row['available_at'] = stamp.strftime('%Y-%m-%d %H:%M:%S') + '.000000000'
        row['content_hash']=digest(row);result.append(row)
    rows = tuple(result)
    inventory_hash(rows)
    return rows


class PublicationTransport(Protocol):
    """Future SSD/content-verified transport; not current financial authority."""
    def read_member(self, row: dict) -> dict | None: ...
    def publish_member(self, root: Path, member: ArchiveMember, row: dict) -> dict: ...
    def read_commit(self, token: str) -> dict | None: ...
    def publish_commit(self, row: dict) -> dict: ...


def publish_archive_plan(root: Path, plan: CanonicalArchivePlan, transport: PublicationTransport):
    """Fresh archives before transport; exact retry/readback and final commit last.

    No concrete database adapter is installed here. The adapter must independently
    validate SSD/schema, compact FULL verified chronology, publish V2 coverage last,
    and verify all original content before returning an exact member receipt.
    """
    if type(plan) is not CanonicalArchivePlan:
        raise ValueError('Exact canonical archive plan required')
    plan.__post_init__()
    fresh=prepare_archive_plan(root,tuple(m.request for m in plan.members))
    if not _equal(fresh.payload(),plan.payload()):
        raise ValueError('Archive changed after planning')
    rows=member_rows(plan)
    for member,row in zip(plan.members,rows):
        stored=transport.read_member(row)
        if stored is None:
            stored=transport.publish_member(Path(root),member,row)
        if not _equal(stored,row):
            raise ValueError('Archive member transport/readback mismatch')
    commit=commit_row(rows)
    stored=transport.read_commit(plan.token)
    if stored is None:
        stored=transport.publish_commit(commit)
    validate_commit(stored, rows)
    return stored
