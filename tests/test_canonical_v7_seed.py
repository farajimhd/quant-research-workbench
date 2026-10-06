"""Read-path tests use synthetic metadata, never publication or financial authority."""
import copy,json
import pytest
from src.backend.canonical_v7_seed import certify_inventory,decode_member_seed,load_seed_batch
from src.backend.structural_v7_seed import _assemble_seed,_band_hash
from src.market_engine.streaming_level_book import EXTRACTION_VERSION
from src.market_engine.canonical_v7_archive_contract import content_hash,commit_row,scope_hash
from src.market_engine.derived_trade_policy import POLICY
from pipelines.market_sip.events.trade_reporting_flags import REVISION


def member(ticker='AAA',**changes):
    row=dict(consolidation_hash='a'*64,ticker=ticker,target_session='2026-08-04',
             seed_session='2026-08-03',available_at='2026-08-04 00:00:00.000000000',
             parent_plan_hash='b'*64,successor_plan_hash='c'*64,source_plan_content_hash='d'*64,
             checkpoint_hash='e'*64,seed_content_hash='5'*64,receipt_hash='f'*64,book_hash='1'*64,
             chronology_hash='2'*64,input_policy=POLICY,reporting_revision=REVISION,scope_hash='3'*64)
    row.update(changes);row['content_hash']=content_hash(row)
    return row


class Reader:
    def __init__(self,rows=None,readonly='1'):
        self.rows=rows or (member(),member('BBB'))
        self.commits=[commit_row(self.rows)]
        self.readonly=readonly;self.queries=[]
    def execute(self,query):
        self.queries.append(query)
        if query=="SELECT getSetting('readonly')":return self.readonly
        assert query.startswith('SELECT ') and 'LIMIT ' in query
        rows=self.commits if 'archive_commit' in query else self.rows
        return '\n'.join(json.dumps(r) for r in rows)


def test_actual_select_path_requires_complete_commit_and_exact_scope():
    reader=Reader();inventory=certify_inventory(reader,consolidation_hash='a'*64)
    assert inventory.token==reader.commits[0]['content_hash']
    selected=inventory.for_scope(target_session='2026-08-04',tickers=('AAA','BBB'),scope_hash='3'*64)
    assert tuple(r['ticker'] for r in selected)==('AAA','BBB')
    assert all(r['successor_plan_hash']=='c'*64 for r in selected)
    assert len(reader.queries)==3
    assert 'LIMIT 3' in reader.queries[-1]


@pytest.mark.parametrize('mode',['absent_commit','duplicate_commit','missing_member','extra_member','tampered_member'])
def test_uncommitted_or_inconsistent_metadata_cannot_certify(mode):
    reader=Reader()
    if mode=='absent_commit':reader.commits=[]
    elif mode=='duplicate_commit':reader.commits*=2
    elif mode=='missing_member':reader.rows=reader.rows[:1]
    elif mode=='extra_member':reader.rows+= (member('CCC'),)
    else:
        reader.rows=tuple(copy.deepcopy(r) for r in reader.rows)
        reader.rows[0]['checkpoint_hash']='4'*64
    with pytest.raises(ValueError):certify_inventory(reader,consolidation_hash='a'*64)


@pytest.mark.parametrize('change',[{'seed_session':'2026-07-31'},
    {'available_at':'2026-08-04 08:00:00.000000001'},
    {'input_policy':'legacy-unfiltered'},{'reporting_revision':'foreign'}, {'scope_hash':'4'*64}])
def test_hash_valid_but_wrong_causal_authority_is_rejected(change):
    inventory=certify_inventory(Reader((member(**change),)),consolidation_hash='a'*64)
    with pytest.raises(ValueError):
        inventory.for_scope(target_session='2026-08-04',tickers=('AAA',),scope_hash='3'*64)


def test_gap_day_and_missing_ticker_never_gain_authority():
    inventory=certify_inventory(Reader(),consolidation_hash='a'*64)
    for day,tickers in [('2026-08-05',('AAA',)),('2026-08-04',('CCC',))]:
        with pytest.raises(ValueError):inventory.for_scope(target_session=day,tickers=tickers,scope_hash='3'*64)


def test_readonly_principal_is_checked_before_metadata_reads():
    reader=Reader(readonly='0')
    with pytest.raises(ValueError):certify_inventory(reader,consolidation_hash='a'*64)
    assert reader.queries==["SELECT getSetting('readonly')"]


def content_fixture():
    cov=dict(ticker='AAA',session_date='2026-08-03',available_at='2026-08-04 00:00:00.000000000',
             source_plan_hash='c'*64,source_checkpoint_hash='e'*64,input_policy=POLICY,
             reporting_revision=REVISION,state='complete',level_count=1,observation_count=1,
             source_extraction_version=EXTRACTION_VERSION,band_config_hash=_band_hash())
    levels=[dict(level_id='one',origin_session='2026-08-03',price=10.,lower=9.9,upper=10.1,
                 association_radius=.2,lifecycle='qualified',historical=1,role='support',
                 parent_level_id='',transition_from='',fit_status='estimated',fit_count=1,
                 fit_distribution='student_t',fit_center=10.,fit_scale=.05,fit_lower=9.9,
                 fit_upper=10.1,fit_resolution=.01,fit_coverage=.8,fit_degrees_of_freedom=4.,
                 fit_scale_at_floor=0)]
    observations=[dict(observation_id='6'*64,level_id='one',price=10.,resolution=.01,
                       at='2026-08-03 14:00:00.000000000',resolved_at='2026-08-03 14:00:10.000000000',
                       role='support',session_date='2026-08-03')]
    from datetime import date
    seed=_assemble_seed('AAA',date(2026,8,4),cov,levels,observations)
    return member(seed_content_hash=seed['checkpoint_hash']),cov,levels,observations,seed


def test_seed_decoder_preserves_raw_checkpoint_and_checks_projected_content():
    row,cov,levels,observations,expected=content_fixture()
    actual=decode_member_seed(row,coverage=cov,levels=levels,observations=observations)
    assert actual==expected
    assert actual['source_checkpoint_hash']==row['checkpoint_hash']
    assert actual['checkpoint_hash']==row['seed_content_hash']!=row['checkpoint_hash']


@pytest.mark.parametrize('family',['levels','observations'])
def test_same_count_changed_sql_content_cannot_reuse_committed_seed_hash(family):
    row,cov,levels,observations,_=content_fixture()
    target=levels if family=='levels' else observations
    target[0]['price']=11.
    with pytest.raises(ValueError,match='reconstructed seed content'):
        decode_member_seed(row,coverage=cov,levels=levels,observations=observations)


def test_coverage_from_another_successor_cannot_be_decoded():
    row,cov,levels,observations,_=content_fixture();cov['source_plan_hash']='7'*64
    with pytest.raises(ValueError,match='committed member identities'):
        decode_member_seed(row,coverage=cov,levels=levels,observations=observations)


def batch_fixture():
    row,cov,levels,observations,expected=content_fixture()
    scope=dict(target_session='2026-08-04',original_market_token='a'*64,scoped_market_token='b'*64,
               original_price_token='c'*64,scoped_price_token='d'*64,exclusion_policy_hash='e'*64)
    row['scope_hash']=scope_hash(scope)
    row['content_hash']=content_hash({k:v for k,v in row.items() if k!='content_hash'})
    inventory=certify_inventory(Reader((row,)),consolidation_hash='a'*64)
    class ContentReader:
        def __init__(self):self.queries=[];self.coverage=[cov];self.levels=levels;self.observations=observations
        def execute(self,query):
            self.queries.append(query)
            if query=="SELECT getSetting('readonly')":return '1'
            assert query.startswith('SELECT ') and 'LIMIT ' in query
            values=(self.coverage if 'coverage_v7' in query else self.observations
                    if 'observations_v7' in query else self.levels)
            return '\n'.join(json.dumps({**r,'ticker':'AAA'}) for r in values)
    return ContentReader(),inventory,scope,expected


def test_bounded_actual_v2_seed_read_path_preserves_projected_seed():
    client,inventory,scope,expected=batch_fixture()
    actual=load_seed_batch(client,inventory,target_session='2026-08-04',tickers=('AAA',),source_scope=scope)
    assert actual=={'AAA':expected} and len(client.queries)==4
    assert all('_v2' in query and 'LIMIT 2' in query for query in client.queries[1:])
    assert all('08:00:00.000000000' in query for query in client.queries[2:])


@pytest.mark.parametrize('mode',['missing_coverage','duplicate_coverage','changed_level','missing_observation','extra_observation','bad_count'])
def test_v2_content_read_rejects_partial_or_changed_authority(mode):
    client,inventory,scope,_=batch_fixture()
    if mode=='missing_coverage':client.coverage=[]
    elif mode=='duplicate_coverage':client.coverage*=2
    elif mode=='changed_level':client.levels[0]['price']=12.
    elif mode=='missing_observation':client.observations=[]
    elif mode=='extra_observation':client.observations*=2
    else:client.coverage[0]['level_count']=True
    with pytest.raises(ValueError):
        load_seed_batch(client,inventory,target_session='2026-08-04',tickers=('AAA',),source_scope=scope)


def test_returned_member_mutation_cannot_change_committed_inventory():
    inventory=certify_inventory(Reader(),consolidation_hash='a'*64)
    original_token=inventory.token
    first,=inventory.for_scope(target_session='2026-08-04',tickers=('AAA',),scope_hash='3'*64)
    first['checkpoint_hash']='9'*64
    again,=inventory.for_scope(target_session='2026-08-04',tickers=('AAA',),scope_hash='3'*64)
    assert again['checkpoint_hash']=='e'*64 and inventory.token==original_token
