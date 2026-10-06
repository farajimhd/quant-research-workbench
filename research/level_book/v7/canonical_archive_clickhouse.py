"""Reviewed archive publication into the existing V2 family, never V1.

This transport installs no schema or grants. A final inventory receipt is not
financial authority. Only explicitly committed dated members are readable.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from . import canonical_archive_publication as archive
from . import direct_publisher as direct
from .clickhouse_persistence import compact_checkpoints, canonical_json, datetime64_ns, epoch_ns
from .campaign_source import literal, REPORTING_REVISION
from scripts.migrate_level_book_v7_to_clickhouse import insert, exclusive_controller
from src.market_engine.level_book_store import verified_book
from src.market_engine.canonical_v7_archive_contract import commit_row, require_hash

MAX_ROWS = 1_000_000
BATCH_ROWS = 512
BATCH_BYTES = 4 * 1024 * 1024
REVIEW_VERSION = 'canonical-v7-archive-apply-review@1'
PRINCIPAL = 'canonical_v7_archive_publisher'


def execution_sources():
    root = Path(__file__).resolve().parents[3]
    names = tuple(name for name, _ in archive.projection_source_identity()) + (
        'research/level_book/v7/canonical_archive_clickhouse.py',
        'research/level_book/v7/direct_publisher.py',
        'scripts/migrate_level_book_v7_to_clickhouse.py',
        'scripts/publish_canonical_v7_archive.py',
        'src/backend/backtest_market_data.py', 'src/backend/backtest_liquidity_price.py',
        'src/backend/backtest_input_scope.py',
        'src/backend/backtest_strategy_one_configuration.py',
        'research/level_book/v7/campaign_source.py',
        'src/market_engine/filtered_v7_history.py',
        'src/market_engine/historical_level_checkpoint.py',
        'src/trading_runtime/arte_journal_schema.py',
        'src/trading_runtime/arte_journal_writer.py')
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in names}


def verify_apply_review(plan, *, review_path, review_hash, exclusion_path):
    """Pin external review then independently reload the actual source authority.

    No caller configuration, market plan or successful-verifier flag is accepted.
    This must complete before opening an INSERT-capable transport.
    """
    require_hash(review_hash)
    raw = Path(review_path).read_bytes()
    if sha256(raw).hexdigest() != review_hash:
        raise ValueError('Apply review bytes differ')
    review = json.loads(raw)
    keys = {'schema','commit','plan_hash','sources','configuration_number',
            'configuration_revision_id','configuration_payload_hash','exclusion_hash'}
    if type(review) is not dict or set(review) != keys or review['schema'] != REVIEW_VERSION:
        raise ValueError('Apply review schema differs')
    for key in ('plan_hash','configuration_payload_hash','exclusion_hash'):
        require_hash(review[key])
    if type(review['sources']) is not dict or type(review['configuration_revision_id']) is not str:
        raise ValueError('Apply review identity types differ')
    if review['plan_hash'] != plan.token or review['sources'] != execution_sources():
        raise ValueError('Reviewed plan/producer/kernel/source bytes differ')
    commit = review['commit']
    if type(commit) is not str or len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('Exact committed execution identity required')
    root = Path(__file__).resolve().parents[3]
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],text=True,encoding='utf-8').strip()
    if git('rev-parse','HEAD') != commit or git('status','--porcelain'):
        raise ValueError('Apply requires clean exact reviewed source')
    remote = git('ls-remote','origin',git('symbolic-ref','--short','HEAD'))
    if not remote or remote.split()[0] != commit:
        raise ValueError('Reviewed execution commit is not pushed on its configured branch')
    if sha256(Path(exclusion_path).read_bytes()).hexdigest() != review['exclusion_hash']:
        raise ValueError('Dated exclusion policy differs')
    if os.environ.get('BACKTEST_INPUT_EXCLUSIONS_FILE') != str(exclusion_path):
        raise ValueError('Explicit runtime exclusion binding differs')
    number = review['configuration_number']
    if type(number) is not int or number <= 0:
        raise ValueError('Configuration identity must be exact positive integer')
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    from src.backend.backtest_market_data import (readonly_clickhouse_client,
        certified_market_plan_from_arte, project_market_day_plan, verify_market_day_plan)
    from src.backend.backtest_liquidity_price import certify_price_level_plan
    from src.backend.backtest_input_scope import input_exclusions
    with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
        configured = certify_numbered_configuration(reader, number)
        if (configured.revision()['revision_id'] != review['configuration_revision_id']
                or configured.payload_hash != review['configuration_payload_hash']):
            raise ValueError('Actual sealed configuration differs from reviewed identity')
        for day in sorted({m.request.target_session for m in plan.members}):
            original = certified_market_plan_from_arte(sessions=(day,),tickers=(),configuration=configured.payload)
            verify_market_day_plan(original, reader)
            prices = certify_price_level_plan(original, reader)
            excluded = input_exclusions(day)
            scoped = project_market_day_plan(original,tuple(t for t in original.tickers if t not in excluded)) if excluded else original
            scoped_prices = prices.projected(scoped)
            group = [m.request for m in plan.members if m.request.target_session == day]
            if {r.ticker for r in group} != set(scoped.tickers):
                raise ValueError('Archive requests do not cover the complete retained dated population')
            expected = (original.token,scoped.token,prices.token,scoped_prices.token,review['exclusion_hash'])
            if any(tuple(getattr(r,k) for k in archive.SOURCE_SCOPE_KEYS[1:]) != expected for r in group):
                raise ValueError('Actual original/scoped market/price authority differs from plan claims')
    return review


def producer_client():
    """Existing private credential loader; no secret creation/copy/error values."""
    from src.trading_runtime.arte_journal_writer import _dedicated_clickhouse_credentials
    from research.mlops.clickhouse import ClickHouseHttpClient
    from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _restrict_secret_file
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import URL
    import platform
    path=Path(os.environ.get('CANONICAL_V7_ARCHIVE_PUBLISHER_CREDENTIAL_FILE',''))
    if (platform.node().upper()!='DESKTOP-SAAI85T' or not path.is_file()
            or path.resolve().parent!=SECRET_ROOT.resolve()):
        raise ValueError('Apply requires an existing private workstation producer credential file')
    _restrict_secret_file(path)
    url,user,password = _dedicated_clickhouse_credentials(
        'CANONICAL_V7_ARCHIVE_PUBLISHER_CLICKHOUSE_', 'CANONICAL_V7_ARCHIVE_PUBLISHER_CREDENTIAL_FILE')
    if user != PRINCIPAL or url!=URL or not password:
        raise ValueError('Canonical archive producer principal differs')
    return ClickHouseHttpClient(url,user,password,timeout_seconds=300,persistent=True,
        default_query_params={'max_threads':2,'max_memory_usage':2*1024**3})


def installation_plan():
    """Concrete operator-only plan; neither runner DDL grants nor execution."""
    tables=(direct.LEVELS,direct.OBSERVATIONS,direct.COVERAGE,
            'arte.'+archive.PROVENANCE.name,'arte.'+archive.COMMIT.name)
    return {'principal':PRINCIPAL,'ddl':direct.ddl()+tuple(t.ddl() for t in archive.TABLES),
        'grants':tuple((permission,table) for table in tables for permission in ('SELECT','INSERT')),
        'metadata_select':('storage_policies','tables','columns','parts','data_skipping_indices'),
        'credential_file':PRINCIPAL+'.env','credential_namespace':'CANONICAL_V7_ARCHIVE_PUBLISHER_CLICKHOUSE_',
        'storage_policy':'live_market_ssd','creates_or_executes':False}


def _rows(client, sql):
    raw = client.execute(sql + ' SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')
    if len(raw.encode()) > 256 * 1024 * 1024:
        raise ValueError('Typed readback exceeds bounded response size')
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) > MAX_ROWS:
        raise ValueError('Typed readback exceeds bounded row count')
    return rows


def storage_preflight(client):
    direct.storage_preflight(client,create=False)
    from src.trading_runtime.arte_journal_schema import storage_preflight as typed_preflight
    typed_preflight(client,tables=archive.TABLES)
    if client.execute('SELECT currentUser()').strip() != PRINCIPAL:
        raise ValueError('Publication requires the exact dedicated producer principal')
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import PrincipalPlan, _effective_grants
    plan=installation_plan()
    names=frozenset(table.split('.')[1] for _,table in plan['grants'])
    grants=PrincipalPlan('canonical_archive',PRINCIPAL,names,names,frozenset(plan['metadata_select']))
    effective=_effective_grants(client,grants)
    required={(permission,'arte',name) for name in names for permission in ('SELECT','INSERT')}
    if not required<=effective:
        raise ValueError('Producer effective grants lack the exact data-table closure')
    for table in (direct.LEVELS,direct.OBSERVATIONS,direct.COVERAGE,
                  'arte.'+archive.PROVENANCE.name,'arte.'+archive.COMMIT.name):
        for permission in ('SELECT','INSERT'):
            if client.execute(f'CHECK GRANT {permission} ON {table}').strip() != '1':
                raise ValueError('Dedicated producer lacks a required exact table permission')


def ticker_content(root, members):
    """Original full chronology through max prior, not thirteen snapshot streams."""
    latest = max(members,key=lambda m:m.seed_session)
    parent = archive.read(Path(root)/latest.request.parent_relative/'plan.json')
    output, successor = archive.successor(Path(root),parent,latest.request.ticker)
    directory = next(r['directory'] for r in parent['rows'] if r['ticker']==latest.request.ticker)
    target = output/'tickers'/directory
    source = archive.read(target/'source-plan.json')
    days = [m['source_date'] for m in source['days'] if m['source_date'] <= latest.seed_session]
    def books():
        chronology=[];checkpoint=None;book_hash=None;input_rows=0
        for day in days:
            receipt_path=target/'receipts'/f'{day}.json'
            receipt = archive.read(receipt_path)
            if receipt['state']=='complete':
                path=target/'books'/f'{day}.json.gz';book=verified_book(path)
                input_rows+=sum(1+len(level.get('observations') or ()) for level in book.get('levels') or ())
                if input_rows>MAX_ROWS:
                    raise ValueError('Ticker chronology exceeds the bounded compaction input budget')
                checkpoint=book['checkpoint_hash'];book_hash=archive._file_hash(path)
                yield book
            elif receipt['state']!='empty':
                raise ValueError('Archive state changed during compaction')
            chronology.append((day,archive._file_hash(receipt_path),checkpoint,book_hash))
            for member in members:
                if member.seed_session==day and (archive.digest(chronology)!=member.chronology_hash
                        or len(chronology)!=member.chronology_sessions):
                    raise ValueError('Archive chronology bytes changed during compaction')
    levels,observations,coverage = compact_checkpoints(books(),latest.successor_plan_hash)
    if any(len(rows)>MAX_ROWS for rows in (levels,observations,coverage)):
        raise ValueError('Ticker compacted inventory exceeds bounded unit')
    by_day = {row['session_date']:row for row in coverage}
    selected=[]
    from src.backend.swing_book_source import session_bounds
    for member in members:
        if member.seed_session in by_day:
            row=dict(by_day[member.seed_session])
        else:
            # An actual verified empty receipt carries prior state. Never invent
            # empty geometry or publish a fabricated checkpoint into chronology.
            receipt=archive.read(target/'receipts'/f'{member.seed_session}.json')
            if receipt['state']!='empty':raise ValueError('Requested coverage has no genuine completed receipt')
            prior=max((r for r in coverage if r['session_date']<member.seed_session),key=lambda r:r['session_date'])
            stamp=epoch_ns(session_bounds(member.seed_session)[1].timestamp())
            row=dict(prior,session_date=member.seed_session,state='empty',
                source_input_hash=receipt['source_hash'],parent_checkpoint_hash=member.checkpoint_hash,
                publication_revision=stamp,available_at=datetime64_ns(stamp))
        if row['source_checkpoint_hash']!=member.checkpoint_hash:
            raise ValueError('Compacted coverage differs from original checkpoint')
        row.update(reporting_revision=REPORTING_REVISION,published_at=row['available_at'])
        from src.backend.structural_v7_seed import _assemble_seed
        from datetime import date
        fence=row['available_at']
        active=lambda rows:[r for r in rows if r['valid_from']<=fence and (r['valid_to'] is None or r['valid_to']>fence)]
        seed=_assemble_seed(member.request.ticker,date.fromisoformat(member.request.target_session),row,
            sorted(active(levels),key=lambda r:r['level_id']),
            sorted(active(observations),key=lambda r:(r['level_id'],r['observation_id'])))
        if seed['checkpoint_hash']!=member.seed_content_hash:
            raise ValueError('Full chronology projected seed differs from reviewed dated member')
        selected.append(row)
    return levels,observations,selected


class ArchiveClickHouseTransport:
    """Concrete SQL transport; construction itself grants no source authority."""
    def __init__(self,client,root,plan):
        self.client=client;self.root=Path(root);self.plan=plan;self._ticker=None;self._coverage=None

    def _reconcile(self,table,expected,where):
        if any(len(canonical_json(row).encode())>BATCH_BYTES for row in expected):
            raise ValueError('A typed row exceeds the insert byte bound')
        columns=tuple(expected[0]) if expected else ()
        final=' FINAL' if table in (direct.LEVELS,direct.OBSERVATIONS,direct.COVERAGE) else ''
        query='SELECT '+(','.join(columns) if columns else '*')+f' FROM {table}{final} WHERE {where} LIMIT {MAX_ROWS+1}'
        observed=_rows(self.client,query)
        wanted={canonical_json(row):row for row in expected}
        if len(wanted)!=len(expected):raise ValueError('Duplicate expected typed content')
        seen=[canonical_json(row) for row in observed]
        if len(set(seen))!=len(seen) or any(key not in wanted for key in seen):
            raise ValueError('Existing V2 content is foreign, duplicate or unresolved')
        seen_keys=set(seen)
        missing=[row for key,row in wanted.items() if key not in seen_keys]
        if missing:insert(self.client,table,missing,archive.digest((self.plan.token,table,where)),batch_rows=BATCH_ROWS,batch_bytes=BATCH_BYTES)
        after=_rows(self.client,query)
        if sorted(map(canonical_json,after))!=sorted(wanted):
            raise ValueError('Exact typed content readback failed')

    def read_member(self,row):
        found=_rows(self.client,f"SELECT {','.join(row)} FROM arte.{archive.PROVENANCE.name} WHERE consolidation_hash={literal(row['consolidation_hash'])} AND ticker={literal(row['ticker'])} AND target_session=toDate({literal(row['target_session'])}) LIMIT 2")
        if len(found)>1:raise ValueError('Duplicate committed member')
        if found and not archive._equal(found[0],row):raise ValueError('Foreign member provenance')
        # Existing provenance never suppresses content revalidation on restart.
        return None

    def publish_member(self,root,member,row):
        ticker=member.request.ticker
        if ticker!=self._ticker:
            group=tuple(m for m in self.plan.members if m.request.ticker==ticker)
            levels,observations,coverage=ticker_content(root,group)
            where='ticker='+literal(ticker)
            self._reconcile(direct.LEVELS,levels,where)
            self._reconcile(direct.OBSERVATIONS,observations,where)
            self._coverage={r['session_date']:r for r in coverage};self._ticker=ticker
        # Content fully read back before coverage and provenance.
        self._reconcile(direct.COVERAGE,[self._coverage[member.seed_session]],
            f'ticker={literal(ticker)} AND session_date=toDate({literal(member.seed_session)})')
        self._reconcile('arte.'+archive.PROVENANCE.name,[row],
            f"consolidation_hash={literal(row['consolidation_hash'])} AND ticker={literal(ticker)} AND target_session=toDate({literal(row['target_session'])})")
        return dict(row)

    def read_commit(self,token):
        if token!=self.plan.token:raise ValueError('Foreign inventory commit identity')
        rows=_rows(self.client,f"SELECT {','.join(dict(archive.COMMIT.columns))} FROM arte.{archive.COMMIT.name} WHERE consolidation_hash={literal(token)} LIMIT 2")
        if len(rows)>1:raise ValueError('Duplicate final inventory commit')
        if rows:self._verify_inventory()
        return rows[0] if rows else None

    def _verify_inventory(self):
        expected=archive.member_rows(self.plan)
        rows=_rows(self.client,f"SELECT {','.join(dict(archive.PROVENANCE.columns))} FROM arte.{archive.PROVENANCE.name} WHERE consolidation_hash={literal(self.plan.token)} ORDER BY ticker,target_session LIMIT {len(expected)+1}")
        if tuple(sorted(rows,key=lambda row:(row['ticker'],row['target_session'])))!=expected:
            raise ValueError('Published inventory membership is missing, extra or foreign')

    def publish_commit(self,row):
        self._verify_inventory()
        self._reconcile('arte.'+archive.COMMIT.name,[row],f"consolidation_hash={literal(row['consolidation_hash'])}")
        return self.read_commit(row['consolidation_hash'])


def apply_archive_plan(root,plan,*,review_path,review_hash,exclusion_path):
    verify_apply_review(plan,review_path=review_path,review_hash=review_hash,exclusion_path=exclusion_path)
    from src.runtime_paths import runtime_root
    operational=runtime_root()
    if not operational.is_dir():raise ValueError('Operational runtime root unavailable')
    # One V2 family controller, including overlapping plans. Deduplication tokens
    # cannot replace mutual exclusion for the ordinary MergeTree receipts.
    with exclusive_controller(operational/'level-book-v7'/'canonical-v2-archive-publication.lock'):
        with closing(producer_client()) as client:
            storage_preflight(client)
            result=archive.publish_archive_plan(root,plan,ArchiveClickHouseTransport(client,root,plan))
            storage_preflight(client)
            return result
