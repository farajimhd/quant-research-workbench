"""Structural running checkpoint links; never a committed financial authority.

Projection reuses original cursor, Portfolio and broker content. A future reader
must independently verify the V4 fence and reload all referenced products.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import re
from types import MappingProxyType
from uuid import UUID

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_schema import TABLES as JOURNAL_TABLES
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import _canonical_typed_content
from src.trading_runtime.arte_portfolio_snapshot import (PreparedPortfolioSnapshot,
    _snapshot_rows, _state_hash, _restore_state, prepare_portfolio_snapshot)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    BrokerMatchSnapshotRows, verify_broker_match_snapshot)
from src.trading_runtime.journal_contract import canonical_json

from src.trading_runtime.arte_running_financial_checkpoint_schema import ROOT, ACCOUNT, TABLES
_CURSOR = next(t for t in JOURNAL_TABLES if t.name == 'trading_backtest_cursor_v1')

def _hash(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()

def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError('Running checkpoint UUID is noncanonical')

def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None or value == '0'*64:
        raise ValueError('Running checkpoint digest is invalid')

def _seal(value):
    return MappingProxyType({**value, 'content_hash': _hash(value)})

@dataclass(frozen=True, slots=True)
class RunningFinancialCheckpointRows:
    """Normalized projection only; no write lease or admission approval."""
    root: MappingProxyType
    accounts: tuple[MappingProxyType, ...]

    def __post_init__(self):
        if (type(self.root) is not MappingProxyType or type(self.accounts) is not tuple
                or not self.accounts or len(self.accounts) > 4096
                or any(type(row) is not MappingProxyType for row in self.accounts)):
            raise ValueError('Running checkpoint rows must be immutable and bounded')
        r = self.root
        if set(r) != {n for n,_ in ROOT.columns}:
            raise ValueError('Running checkpoint root keyset differs')
        for name in ('run_id','batch_id','broker_snapshot_id'): _uuid(r[name])
        for name in ('configuration_hash','broker_snapshot_hash','account_hash','content_hash'): _digest(r[name])
        if (type(r['last_sequence']) is not int or not 0 < r['last_sequence'] < 2**64
                or type(r['boundary_ms']) is not int or not 0 < r['boundary_ms'] <= 57_600_000
                or r['boundary_ms'] % 100 or type(r['account_count']) is not int
                or r['account_count'] != len(self.accounts) or type(r['source_cursor']) is not str):
            raise ValueError('Running checkpoint root scalars differ')
        if type(r['session_date']) is not str or type(r['checkpoint_month']) is not str:
            raise ValueError('Running checkpoint dates are noncanonical')
        day = date.fromisoformat(r['session_date'])
        at = market_day_boundary(day,r['boundary_ms']).astimezone(timezone.utc)
        if (day.isoformat() != r['session_date']
                or at.date().replace(day=1).isoformat() != r['checkpoint_month']):
            raise ValueError('Running checkpoint month differs from clock')
        ids=[]
        for i,row in enumerate(self.accounts):
            if (set(row) != {n for n,_ in ACCOUNT.columns}
                    or any(row[n] != r[n] for n in ('run_id','checkpoint_month','batch_id','last_sequence'))
                    or type(row['last_sequence']) is not int or type(row['state_revision']) is not int
                    or row['state_revision'] != r['last_sequence'] or type(row['ordinal']) is not int
                    or row['ordinal'] != i or type(row['account_id']) is not str or not row['account_id']
                    or row['snapshot_at'] != at.isoformat(timespec='microseconds')):
                raise ValueError('Running checkpoint account graph differs')
            _digest(row['state_hash']); _digest(row['content_hash'])
            if row['content_hash'] != _hash({k:v for k,v in row.items() if k != 'content_hash'}):
                raise ValueError('Running checkpoint account content differs')
            ids.append(row['account_id'])
        if ids != sorted(set(ids)):
            raise ValueError('Running checkpoint accounts are duplicate or unordered')
        if (r['account_hash'] != _hash([dict(c) for c in self.accounts])
                or r['content_hash'] != _hash({k:v for k,v in r.items() if k != 'content_hash'})):
            raise ValueError('Running checkpoint root content differs')


def project_running_financial_checkpoint(*, prefix, cursor, configuration_hash,
                                         portfolios, broker):
    if type(prefix) is not V4CommittedPrefix or prefix.status != 'running':
        raise ValueError('Running checkpoint requires exact running V4 prefix')
    _uuid(prefix.run_id); _uuid(prefix.last_batch_id)
    if (type(prefix.last_sequence) is not int or not 0 < prefix.last_sequence < 2**64
            or type(prefix.batch_ids) is not tuple or not prefix.batch_ids
            or len(set(prefix.batch_ids)) != len(prefix.batch_ids)
            or prefix.batch_ids[-1] != prefix.last_batch_id
            or type(prefix.source_cursor) is not str):
        raise ValueError('Running checkpoint prefix is detached')
    for batch in prefix.batch_ids: _uuid(batch)
    _digest(configuration_hash)
    if type(cursor) is not dict or set(cursor) != {n for n,_ in _CURSOR.columns}|{'event_sequence'}:
        raise ValueError('Running checkpoint needs complete canonical cursor')
    if (type(cursor['event_sequence']) is not int or cursor['event_sequence'] != prefix.last_sequence
            or cursor['run_id'] != prefix.run_id or cursor['batch_id'] != prefix.last_batch_id
            or type(cursor['boundary_ms']) is not int or not 0 < cursor['boundary_ms'] <= 57_600_000
            or cursor['boundary_ms'] % 100 or cursor['account_id'] != ''):
        raise ValueError('Running checkpoint cursor differs from prefix')
    if type(cursor['session_date']) is not str:
        raise ValueError('Running checkpoint session date is not canonical')
    day = date.fromisoformat(cursor['session_date'])
    if day.isoformat() != cursor['session_date']: raise ValueError('Noncanonical session date')
    at = market_day_boundary(day,cursor['boundary_ms']).astimezone(timezone.utc)
    month = at.date().replace(day=1).isoformat()
    if cursor['event_month'] != month: raise ValueError('Cursor month differs')
    content = {k:v for k,v in cursor.items() if k not in ('content_hash','event_sequence')}
    if _hash(_canonical_typed_content(_CURSOR.name,content,stored_utc=True)) != cursor['content_hash']:
        raise ValueError('Running checkpoint cursor content mismatch')
    if type(broker) is not BrokerMatchSnapshotRows:
        raise ValueError('Running checkpoint needs typed broker product')
    checked = verify_broker_match_snapshot(broker)
    b = checked.snapshot
    if (b['run_id'] != prefix.run_id or type(b['checkpoint_sequence']) is not int
            or b['checkpoint_sequence'] != prefix.last_sequence or b['session_date'] != day.isoformat()
            or b['boundary_ms'] != cursor['boundary_ms'] or b['snapshot_month'] != month):
        raise ValueError('Running checkpoint broker clock differs')
    _uuid(b['snapshot_id']); _digest(b['content_hash'])
    if type(portfolios) is not tuple or not portfolios or len(portfolios) > 4096:
        raise ValueError('Running checkpoint needs bounded typed account captures')
    ids = tuple(p.account_id for p in portfolios if type(p) is PreparedPortfolioSnapshot)
    broker_ids = tuple(sorted(row['account_id'] for row in checked.accounts))
    if (len(ids) != len(portfolios) or any(type(i) is not str or not i for i in ids)
            or ids != tuple(sorted(set(ids))) or ids != broker_ids):
        raise ValueError('Running checkpoint account graph differs')
    common = dict(run_id=prefix.run_id,checkpoint_month=month,batch_id=prefix.last_batch_id,
                  last_sequence=prefix.last_sequence)
    children=[]
    for ordinal,p in enumerate(portfolios):
        p.__post_init__()
        if (p.run_id != prefix.run_id or type(p.state_revision) is not int
                or p.state_revision != prefix.last_sequence or p.snapshot_month != month
                or p.rows.account['snapshot_at'] != at.isoformat(timespec='microseconds')):
            raise ValueError('Running checkpoint Portfolio clock differs')
        families = _snapshot_rows(p.run_id,p.account_id,p.state_revision,p.snapshot_month,p.rows)
        state = _restore_state(families, ({**asdict(p.selected_policy),
                 'identity':p.selected_policy.identity} if p.selected_policy is not None else None))
        rebuilt = prepare_portfolio_snapshot(run_id=p.run_id,account_id=p.account_id,
            state_revision=p.state_revision,snapshot_at=at,state=state)
        if rebuilt != p: raise ValueError('Running checkpoint Portfolio content differs')
        children.append(_seal({**common,'ordinal':ordinal,'account_id':p.account_id,
            'state_revision':p.state_revision,'state_hash':_state_hash(families),
            'snapshot_at':at.isoformat(timespec='microseconds')}))
    root = _seal({**common,'source_cursor':prefix.source_cursor,'session_date':day.isoformat(),
        'boundary_ms':cursor['boundary_ms'],'configuration_hash':configuration_hash,
        'broker_snapshot_id':b['snapshot_id'],'broker_snapshot_hash':b['content_hash'],
        'account_count':len(children),'account_hash':_hash([dict(c) for c in children])})
    return RunningFinancialCheckpointRows(root,tuple(children))
