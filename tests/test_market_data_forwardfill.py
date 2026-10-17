from contextlib import nullcontext
from datetime import date
import json
from pathlib import Path

import pytest

from scripts import market_data_forwardfill as flow
from scripts.run_market_data_pipeline import arguments
from pipelines.market_sip.events import clickhouse_build_daily_session_bars as daily


def options(root, *extra):
    return arguments(['forwardfill','--run-dir',str(root),'--allow-acquisition',*extra])


def test_forwardfill_requires_acquisition_opt_in_and_exact_population(tmp_path):
    for extra in ([],['--allow-acquisition','--identity-policy','current_conid_retrospective_v1'],
                  ['--allow-acquisition','--allow-carried-forward-universe']):
        with pytest.raises(SystemExit):arguments(['forwardfill','--run-dir',str(tmp_path),*extra])
    a=options(tmp_path)
    assert a.step=='bars'
    values=flow.ingestion_arguments(a)
    parsed=flow.ingestion.parse_args(values)
    assert parsed.skip_bars and parsed.flatfiles_root_win==str(a.cache_root)


def test_plan_does_not_contact_provider_or_build(tmp_path,monkeypatch):
    monkeypatch.setattr(flow.ingestion,'main',lambda *a,**k:pytest.fail('provider contacted'))
    assert flow.run(options(tmp_path,'--plan-only'),lambda a:pytest.fail('builder called'))==0
    assert not (tmp_path/'forwardfill-request.json').exists()
    assert json.loads((tmp_path/'forwardfill-plan.json').read_text())['daily_target'].startswith('arte.')


def test_crash_after_ingestion_retains_handoff_and_resume_does_not_drop_day(tmp_path,monkeypatch):
    monkeypatch.setattr(flow.bars,'build_lock',lambda p:nullcontext())
    day='2026-10-05';calls=[]
    def ingest(values,*,acquisition_approved,selected_days):
        assert acquisition_approved
        selected_days((day,))
        assert day in json.loads((tmp_path/'forwardfill-queue.json').read_text())['selected']
    monkeypatch.setattr(flow.ingestion,'main',ingest)
    monkeypatch.setattr(flow,'daily_product',lambda a,d,p:calls.append(('daily',d)))
    assert flow.run(options(tmp_path),lambda a:130)==130
    assert json.loads((tmp_path/'forwardfill-queue.json').read_text())['complete']==[]
    def build(a):
        assert a.start_date==a.end_date==date(2026,10,5)
        calls.append(('bars',str(a.start_date)));return 0
    assert flow.run(options(tmp_path,'--resume'),build)==0
    assert calls==[('bars',day),('daily',day)]
    assert flow.run(options(tmp_path,'--resume'),lambda a:pytest.fail('repeated completed day'))==0


def test_ingestion_failure_never_starts_derived_writer(tmp_path,monkeypatch):
    monkeypatch.setattr(flow.bars,'build_lock',lambda p:nullcontext())
    def fail(*args,**kwargs):raise RuntimeError('source certification failed')
    monkeypatch.setattr(flow.ingestion,'main',fail)
    with pytest.raises(RuntimeError,match='source certification'):
        flow.run(options(tmp_path),lambda a:pytest.fail('derived writer started'))


def test_daily_sql_routes_only_derived_tables_to_arte_and_excludes_unknown_delayed():
    a=daily.parse_args(['--start-date','2026-10-05','--end-date','2026-10-06',
        '--target-database','arte','--storage-policy','live_market_ssd','--tickers','AAPL',
        '--feature-version',flow.DAILY_VERSION])
    a.tradable_timely=True;a.population_hash='a'*64
    left=date(2026,10,5);right=date(2026,10,6)
    sql=daily.insert_session_bars_sql(a,left,right)
    assert 'INSERT INTO `arte`.`daily_session_bars_by_symbol_time_v1`' in sql
    assert '`market_sip_compact`.`events_2026`' in sql
    assert 'bitAnd(event_meta,192)=64' in sql and "'AAPL'" in sql
    class Client:
        def execute(self,query):
            assert 'bitAnd(event_meta,192)=64' in query
            return '42\n'
    assert daily.source_event_count(Client(),a,left,right)==42
    assert daily._build_version(a).endswith('a'*64)
    a.tickers=''
    with pytest.raises(ValueError,match='certified population'):
        daily.insert_session_bars_sql(a,left,right)


def test_default_updater_routes_to_shared_tradable_pipeline(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(flow,'derive_imported_days',lambda a,days:seen.extend(days))
    a=flow.ingestion.parse_args([])
    flow.ingestion.build_updated_bars(None,a,[type('Day',(),{'source_date':'2026-10-05'})()],tmp_path/'report.jsonl')
    assert seen==['2026-10-05']
