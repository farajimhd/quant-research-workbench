"""Prepared source scope fixtures; no SQL, archives or producer invocation."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from research.level_book.v7 import canonical_scoped_campaign as m

H = 'a'*64


@pytest.fixture
def declaration(tmp_path, monkeypatch):
    exclusions = tmp_path/'exclusions.json'
    exclusions.write_text('[]')
    scope = dict(schema=m.VERSION, role='development', sessions=['2026-08-04','2026-08-05'],
        configuration_number=9001, configuration_revision_id='fixture-revision',
        configuration_payload_hash=H, exclusion_hash=sha256(exclusions.read_bytes()).hexdigest())
    path = tmp_path/'scope.json'
    path.write_text(json.dumps(scope))
    monkeypatch.setenv('BACKTEST_INPUT_EXCLUSIONS_FILE', str(exclusions))
    return scope, path, exclusions


def test_declaration_exact_pinned_bytes(declaration):
    scope, path, exclusions = declaration
    assert m.read_scope(path, sha256(path.read_bytes()).hexdigest(), exclusions) == scope


@pytest.mark.parametrize('field,value', [('role','validation'), ('configuration_number',True),
    ('configuration_payload_hash','0'*64), ('sessions',['2026-08-05','2026-08-04']),
    ('sessions',['2026-08-01']), ('sessions',[])])
def test_invalid_declaration(declaration, field, value):
    scope, path, exclusions = declaration
    scope[field] = value
    path.write_text(json.dumps(scope))
    with pytest.raises(ValueError):
        m.read_scope(path, sha256(path.read_bytes()).hexdigest(), exclusions)


def test_missing_hash_and_exclusion_binding(declaration, monkeypatch):
    _, path, exclusions = declaration
    with pytest.raises(ValueError, match='bytes differ'):
        m.read_scope(path, H, exclusions)
    monkeypatch.delenv('BACKTEST_INPUT_EXCLUSIONS_FILE')
    with pytest.raises(ValueError, match='environment'):
        m.read_scope(path, sha256(path.read_bytes()).hexdigest(), exclusions)


def test_original_proofs_precede_scope_projection(declaration, monkeypatch):
    import src.backend.backtest_strategy_one_configuration as config
    import src.backend.backtest_market_data as market
    import src.backend.backtest_liquidity_price as price
    import src.backend.backtest_input_scope as exclusion
    scope = declaration[0]
    calls = []
    original = NS(tickers=('ALPHA','BETA'), token='original')
    projected = NS(tickers=('ALPHA',), token='scoped')
    prices = NS(token='price-original', projected=lambda p: calls.append('price-project') or NS(token='price-scoped'))
    monkeypatch.setattr(config, 'certify_numbered_configuration', lambda *a: NS(payload={},payload_hash=H,revision=lambda:dict(revision_id='fixture-revision')))
    monkeypatch.setattr(market, 'certified_market_plan_from_arte', lambda **k: calls.append('market-load') or original)
    monkeypatch.setattr(market, 'verify_market_day_plan', lambda *a: calls.append('full-market-proof'))
    monkeypatch.setattr(price, 'certify_price_level_plan', lambda *a: calls.append('full-price-proof') or prices)
    monkeypatch.setattr(exclusion, 'input_exclusions', lambda d: calls.append('exclusion') or ('BETA',))
    monkeypatch.setattr(market, 'project_market_day_plan', lambda *a: calls.append('market-project') or projected)
    result = m.certified_scopes(object(), scope)
    assert calls == ['market-load','full-market-proof','full-price-proof','exclusion','market-project','price-project']*2
    assert all(r['tickers'] == ['ALPHA'] for r in result)
    monkeypatch.setattr(market, 'verify_market_day_plan', lambda *a: (_ for _ in ()).throw(ValueError('full proof rejected')))
    calls.clear()
    with pytest.raises(ValueError, match='full proof'):
        m.certified_scopes(object(), scope)
    assert calls == ['market-load']


@pytest.fixture
def producer(tmp_path, declaration, monkeypatch):
    scope = declaration[0]
    scopes = [dict(target_session=d,tickers=['ALPHA'] if i==0 else ['BETA'],
                   original_market_token=H,scoped_market_token=H,original_price_token=H,
                   scoped_price_token=H,exclusion_policy_hash=scope['exclusion_hash'])
              for i,d in enumerate(scope['sessions'])]
    parent = dict(start='2026-07-31',end='2026-08-06',rules=[], rows=[
        dict(ticker=t,coverage=dict(days=5),status='queued') for t in ('ALPHA','BETA')])
    parent['plan_hash'] = m.c.digest(parent)
    folder = tmp_path/'parent'
    m.c.write(folder/'plan.json', parent)
    rows = [dict(ticker=t,state='queued',parent='parent',parent_hash=parent['plan_hash'],
        plan_hash=H,output='output-'+t,directory=t,before='2026-08-07',sessions=5) for t in ('ALPHA','BETA')]
    inventory = dict(rows=rows,catalog_hash=H)
    monkeypatch.setattr(m.filtered,'make_plan', lambda root: deepcopy(inventory))
    monkeypatch.setattr(m,'successor', lambda root,p,t: (root/('output-'+t),dict(plan_hash=H,input_policy=m.c.POLICY,reporting_revision=m.source.REPORTING_REVISION)))
    monkeypatch.setattr(m,'kernel',lambda:dict(source_files={}))
    monkeypatch.setattr(m.c,'hashes',lambda:{})
    monkeypatch.setattr(m.filtered,'execution_hashes',lambda:{})
    names = ['2026-07-31','2026-08-03','2026-08-04','2026-08-05','2026-08-06']
    def query(reader, sql):
        if 'GROUP BY ticker' in sql:return [dict(days=5)]
        if sql == m.c.RULE_SQL:return []
        if 'historical_trade_reporting' in sql:return [dict(source_date=d,status='complete') for d in names]
        ticker = 'ALPHA' if "'ALPHA'" in sql else 'BETA'
        return [dict(ticker=ticker,source_date=d) for d in names]
    monkeypatch.setattr(m,'_query',query)
    return tmp_path,scope,scopes,inventory,query


def test_full_union_true_prior_prefix_and_end(producer):
    root,scope,scopes,_,_ = producer
    plan = m.prepare_manifest(root,scope,scopes,object())
    assert [(r['ticker'],r['before'],r['sessions']) for r in plan['rows']] == [('BETA','2026-08-05',3),('ALPHA','2026-08-04',2)]
    units = plan['scoped_authority']['units']
    assert units[0]['through'] == '2026-08-03'
    assert units[0]['source_sessions'] == ['2026-07-31','2026-08-03']
    assert units[0]['full_metadata_end'] == '2026-08-06'
    assert plan['manifest_hash'] == m.c.digest({k:v for k,v in plan.items() if k!='manifest_hash'})


def test_canary_pins_full_retained_parent_union(producer):
    root,scope,scopes,_,_ = producer
    plan = m.prepare_manifest(root,scope,scopes,object(),canary_ticker='ALPHA')
    assert len(plan['rows']) == 1
    assert len(plan['scoped_authority']['retained_parent_inventory']) == 2
    assert plan['scoped_authority']['selection'] == dict(mode='canary',ticker='ALPHA')


@pytest.mark.parametrize('mode', ['missing-parent','deferred-parent','prior-gap','duplicate','foreign','reporting','policy','date-order'])
def test_source_gaps_and_drift_reject(producer,monkeypatch,mode):
    root,scope,scopes,inventory,query = producer
    if mode == 'missing-parent':inventory['rows'].pop()
    if mode == 'deferred-parent':inventory['rows'][1]['state']='deferred'
    if mode == 'date-order':scopes.reverse()
    if mode == 'policy':monkeypatch.setattr(m,'successor',lambda root,p,t:(root/('output-'+t),dict(plan_hash=H,input_policy='legacy',reporting_revision=m.source.REPORTING_REVISION)))
    if mode in ('prior-gap','duplicate','foreign','reporting'):
        def bad(reader,sql):
            rows = query(reader,sql)
            if 'ORDER BY source_date' in sql and 'historical_trade_reporting' not in sql:
                if mode=='prior-gap':rows[1]['source_date']='2026-08-01'
                if mode=='duplicate':rows[1]=dict(rows[0])
                if mode=='foreign':rows[1]['ticker']='FOREIGN'
            if mode=='reporting' and 'historical_trade_reporting' in sql:rows[0]['status']='missing'
            return rows
        monkeypatch.setattr(m,'_query',bad)
    with pytest.raises(ValueError):m.prepare_manifest(root,scope,scopes,object())


def test_atomic_immutable_restart_and_no_producer(producer,monkeypatch):
    root,scope,scopes,_,_ = producer
    plan = m.prepare_manifest(root,scope,scopes,object())
    monkeypatch.setattr(m,'build_manifest',lambda *a,**k:deepcopy(plan))
    monkeypatch.setattr(m.filtered,'checked_plan',lambda folder:m.c.read(folder/'plan.json'))
    out=root/'export'
    assert m.export_manifest(root,out,'x',H,'y')==plan
    assert m.export_manifest(root,out,'x',H,'y')==plan
    assert m.verify_export(root,out,'x',H,'y')==plan
    assert not list(out.glob('*.tmp'))
    plan['rows'][0]['sessions']+=1
    with pytest.raises(ValueError,match='Immutable'):m.export_manifest(root,out,'x',H,'y')
    with pytest.raises(ValueError,match='changed'):m.verify_export(root,out,'x',H,'y')


def test_stale_cached_kernel_rejects_before_source_queries(producer,monkeypatch):
    root,scope,scopes,_,_ = producer
    monkeypatch.setattr(m.c,'hashes',lambda:{'changed':H})
    with pytest.raises(ValueError,match='Cached numerical kernel'):
        m.prepare_manifest(root,scope,scopes,object())


def test_parent_cache_once_each_export_and_fresh_after_mutation(producer,monkeypatch):
    root,scope,scopes,_,_=producer
    original=m.c.read;reads=[]
    def read(path):reads.append(path);return original(path)
    monkeypatch.setattr(m.c,'read',read)
    m.prepare_manifest(root,scope,scopes,object())
    assert len(reads)==1
    m.prepare_manifest(root,scope,scopes,object())
    assert len(reads)==2
    parent=original(root/'parent'/'plan.json');parent['end']='2026-08-07'
    m.c.write(root/'parent'/'plan.json',parent,immutable=False)
    with pytest.raises(ValueError,match='parent identity'):m.prepare_manifest(root,scope,scopes,object())


def test_same_cached_parent_cannot_accept_foreign_expected_hash(producer):
    root,scope,scopes,inventory,_=producer
    inventory['rows'][1]['parent_hash']='b'*64
    with pytest.raises(ValueError,match='parent identity'):
        m.prepare_manifest(root,scope,scopes,object())


def test_cli_help_and_bad_output_have_no_db_or_state(tmp_path,monkeypatch,capsys):
    import scripts.prepare_canonical_v7_scoped_campaign as cli
    monkeypatch.setattr(cli,'export_manifest',lambda *a,**k:pytest.fail('must not access database'))
    with pytest.raises(SystemExit) as stopped:cli.main(['--help'])
    assert stopped.value.code==0
    assert cli.main(['--archive-root',str(tmp_path),'--output',str(tmp_path/'bad'),
        '--development-scope','absent','--development-scope-sha256',H,'--exclusions','absent'])==1
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('kind', ['repository','outside','unavailable'])
def test_cli_operational_root_guard_before_db(tmp_path,monkeypatch,kind):
    import scripts.prepare_canonical_v7_scoped_campaign as cli
    import src.runtime_paths as paths
    operational=tmp_path/'runtime'
    operational.mkdir()
    monkeypatch.setattr(paths,'runtime_root',lambda:operational)
    monkeypatch.setattr(paths,'WORKSTATION_RUNTIME_ROOT',tmp_path/'missing-workstation')
    if kind=='repository':
        archive=m.c.REPO
        monkeypatch.setattr(paths,'runtime_root',lambda:m.c.REPO)
    elif kind=='outside':archive=tmp_path/'outside'
    else:
        archive=operational/'archive'
        operational.rmdir()
    output=archive/'filtered-preparation-campaigns'/'fixture-campaign'
    monkeypatch.setattr(cli,'export_manifest',lambda *a,**k:pytest.fail('must not access DB or source inputs'))
    assert cli.main(['--archive-root',str(archive),'--output',str(output),
        '--development-scope','absent','--development-scope-sha256',H,'--exclusions','absent'])==1
    assert not output.exists()
