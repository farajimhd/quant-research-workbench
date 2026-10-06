"""Shared canonical archive inventory identity; no producer or read authority."""
from datetime import date, datetime
from hashlib import sha256
import json
import re

VERSION = 'canonical-v7-archive-consolidation@1'
MEMBER_TABLE = 'structural_v7_archive_member_v1'
COMMIT_TABLE = 'structural_v7_archive_commit_v1'
MAX_MEMBERS = 100_000  # bounded metadata transport, not an economic parameter
MEMBER_COLUMNS = (
    ('consolidation_hash', 'FixedString(64)'), ('ticker', 'String'),
    ('target_session', 'Date'), ('seed_session', 'Date'),
    ('available_at', "DateTime64(9, 'UTC')"), ('parent_plan_hash', 'FixedString(64)'),
    ('successor_plan_hash', 'FixedString(64)'), ('source_plan_content_hash', 'FixedString(64)'),
    ('checkpoint_hash', 'FixedString(64)'), ('seed_content_hash', 'FixedString(64)'), ('receipt_hash', 'FixedString(64)'),
    ('book_hash', 'FixedString(64)'), ('chronology_hash', 'FixedString(64)'),
    ('input_policy', 'String'), ('reporting_revision', 'String'),
    ('scope_hash', 'FixedString(64)'), ('content_hash', 'FixedString(64)'))
COMMIT_COLUMNS = (
    ('consolidation_hash', 'FixedString(64)'), ('unit_count', 'UInt32'),
    ('member_count', 'UInt32'), ('member_hash', 'FixedString(64)'),
    ('content_hash', 'FixedString(64)'))


def content_hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require_hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None or value == '0' * 64:
        raise ValueError('Invalid canonical archive hash')
    return value


def validate_member(row):
    if type(row) is not dict or set(row) != {name for name,_ in MEMBER_COLUMNS}:
        raise ValueError('Canonical archive member columns differ')
    if any(type(value) is not str for value in row.values()):
        raise ValueError('Canonical archive member requires exact wire strings')
    if re.fullmatch(r'[A-Z0-9.\- ]{1,30}', row['ticker']) is None:
        raise ValueError('Invalid canonical archive member ticker')
    for name in ('target_session','seed_session'):
        if date.fromisoformat(row[name]).isoformat() != row[name]:
            raise ValueError('Invalid canonical archive date')
    if row['seed_session'] >= row['target_session']:
        raise ValueError('Canonical archive seed is not prior')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{9}',row['available_at']) is None:
        raise ValueError('Canonical archive availability requires UTC DateTime64(9) wire form')
    datetime.strptime(row['available_at'][:19], '%Y-%m-%d %H:%M:%S')
    if not row['input_policy'] or not row['reporting_revision']:
        raise ValueError('Missing canonical producer policies')
    for name in ('consolidation_hash','parent_plan_hash','successor_plan_hash','source_plan_content_hash',
                 'checkpoint_hash','seed_content_hash','receipt_hash','book_hash','chronology_hash','scope_hash','content_hash'):
        require_hash(row[name])
    if row['content_hash'] != content_hash({k:v for k,v in row.items() if k != 'content_hash'}):
        raise ValueError('Canonical archive member hash differs')
    return row


def inventory_hash(rows):
    if type(rows) is not tuple or not 0 < len(rows) <= MAX_MEMBERS:
        raise ValueError('Canonical archive inventory is empty or exceeds bound')
    keys=[];tokens=set();lineages={}
    for row in rows:
        validate_member(row);keys.append((row['ticker'],row['target_session']));tokens.add(row['consolidation_hash'])
        identity=(row['parent_plan_hash'],row['successor_plan_hash'],row['source_plan_content_hash'])
        if row['ticker'] in lineages and lineages[row['ticker']] != identity:
            raise ValueError('Canonical archive inventory mixes one ticker lineage')
        lineages[row['ticker']]=identity
    if keys != sorted(set(keys)) or len(tokens) != 1:
        raise ValueError('Canonical archive inventory order/identity differs')
    return content_hash(rows)


def commit_row(rows):
    hashed=inventory_hash(rows)
    row=dict(consolidation_hash=rows[0]['consolidation_hash'],
             unit_count=len({r['ticker'] for r in rows}),member_count=len(rows),member_hash=hashed)
    row['content_hash']=content_hash(row)
    return row


def validate_commit(row, rows):
    if type(row) is not dict or set(row) != {name for name,_ in COMMIT_COLUMNS}:
        raise ValueError('Canonical archive commit columns differ')
    for name in ('unit_count','member_count'):
        if type(row[name]) is not int or not 0 < row[name] <= MAX_MEMBERS:
            raise ValueError('Canonical archive commit count type/value differs')
    for name in ('consolidation_hash','member_hash','content_hash'):
        require_hash(row[name])
    if row != commit_row(rows):
        raise ValueError('Canonical archive committed inventory differs')
    return row



SOURCE_SCOPE_KEYS = ('target_session','original_market_token','scoped_market_token',
                     'original_price_token','scoped_price_token','exclusion_policy_hash')


def scope_hash(value):
    """One independently verified dated population; ticker lineage is separate."""
    if type(value) is not dict or set(value)!=set(SOURCE_SCOPE_KEYS):
        raise ValueError('Canonical archive source scope fields differ')
    if type(value['target_session']) is not str or date.fromisoformat(value['target_session']).isoformat()!=value['target_session']:
        raise ValueError('Canonical archive source scope date differs')
    for name in SOURCE_SCOPE_KEYS[1:]:require_hash(value[name])
    return content_hash(value)
