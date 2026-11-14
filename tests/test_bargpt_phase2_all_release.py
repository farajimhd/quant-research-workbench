"""All-published certification and consumption without liquidity resampling."""
import json
from pathlib import Path
import polars as pl
import pytest
from tests.test_bargpt_phase2_latent_v1 import session
from research.bar_gpt.phase2_v1.data import load_units,checked_unit
from research.bar_gpt.phase2_v1.release import certify,VERSION
from research.bar_gpt.v5.phase2_ar_shards.contract import Geometry
from research.bar_gpt.v5.phase2_ar_shards.ledger import Catalog,save_part,schema
from research.bar_gpt.v5.phase2_ar_shards.storage import read_receipt
from research.bar_gpt.v5.shards.contract import digest,write_json,file_hash


def dataset(root,checkpoint=False):
    root.mkdir()
    plan=dict(jobs=[dict(ticker='TEST',years=[dict(year=2023,liquid=1)])],geometry=Geometry().manifest())
    plan=json.loads(json.dumps(plan));plan['hash']=digest(plan);write_json(root/'plan.json',plan)
    unit=session(root,binding=plan['hash'])
    receipt=read_receipt(unit.path,plan['hash'])
    catalog=Catalog(root);catalog.register(unit.path,receipt)
    selection=catalog.export(plan['hash']);catalog.inspection_catalog(plan)
    stats=catalog.stats()
    if checkpoint:
        d=catalog.ledger.root
        up=save_part(d/'checkpoint-units.parquet',pl.DataFrame(list(catalog.units.values()),schema=schema('units'),orient='row'))
        bp=save_part(d/'checkpoint-blocks.parquet',pl.DataFrame(catalog.blocks,schema=schema('blocks'),orient='row'))
        body=dict(catalog.ledger.manifest,checkpoint=dict(units=[up],blocks=[bp]),parts=[],segments=[],sequence=0)
        body.pop('hash',None);catalog.ledger._commit_manifest(body)
    catalog.close()
    result=dict(state='capacity_reached',failure=None,binding=plan['hash'],selection=selection,**stats)
    write_json(root/'result.json',result);write_json(root/'progress.json',result)
    return unit


@pytest.mark.parametrize('checkpoint',[False,True])
def test_every_published_block_is_certified_even_if_old_selection_is_empty(tmp_path,checkpoint):
    unit=dataset(tmp_path/'source',checkpoint)
    before={p.name:file_hash(p) for p in unit.path.iterdir()}
    cert=certify([unit.root],tmp_path/'release',workers=1)
    assert cert['version']==VERSION and cert['blocks']==1
    assert cert['distribution'][0]['liquid']==1 and cert['distribution'][0]['blocks']==1
    assert cert['all_payload_hashes_recomputed'] is False
    assert len(cert['sampled_publications'])==1
    assert before=={p.name:file_hash(p) for p in unit.path.iterdir()}
    with pytest.raises(ValueError,match='no released selected'):load_units([unit.root])
    units,identity=load_units(release=tmp_path/'release/certificate.json')
    assert len(units)==1 and len(units[0].blocks)==1 and identity['blocks']==1
    assert units[0].receipt_hash==read_receipt(unit.path)['hash']
    checked_unit(units[0])


def test_corrupt_index_source_and_unsupported_mixed_policy_fail_closed(tmp_path):
    unit=dataset(tmp_path/'source');out=tmp_path/'release';cert=certify([unit.root],out,workers=1)
    with pytest.raises(ValueError,match='not both'):load_units([unit.root],out/'certificate.json')
    index=out/cert['roots'][0]['index']['name'];index.write_bytes(index.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='SHA'):load_units(release=out/'certificate.json')


def test_incomplete_root_never_gets_a_certificate(tmp_path):
    unit=dataset(tmp_path/'source');write_json(unit.root/'progress.json',dict(state='running'))
    out=tmp_path/'release'
    with pytest.raises(ValueError,match='active'):certify([unit.root],out,workers=1)
    assert not (out/'certificate.json').exists()


def test_journal_mutation_is_detected_before_release(tmp_path):
    unit=dataset(tmp_path/'source')
    p=next((unit.root/'ledger-file-v1').glob('journal-*.parquet'))
    p.write_bytes(p.read_bytes()+b'changed')
    out=tmp_path/'release'
    with pytest.raises(ValueError,match='SHA'):certify([unit.root],out,workers=1)
    assert not (out/'certificate.json').exists()


def test_completed_root_resume_is_pinned_and_never_repeats_the_ledger_pass(tmp_path,monkeypatch):
    unit=dataset(tmp_path/'source');out=tmp_path/'release'
    import research.bar_gpt.phase2_v1.release as module
    audit=module.sample_audit
    def interrupt(*args):raise TimeoutError('fixture interruption after completed root audit')
    monkeypatch.setattr(module,'sample_audit',interrupt)
    with pytest.raises(TimeoutError):certify([unit.root],out,workers=1)
    assert (out/'root-00.proof.json').exists() and not (out/'certificate.json').exists()
    monkeypatch.setattr(module,'sample_audit',audit)
    def no_repeat(*args,**kw):raise AssertionError('Completed ledger was needlessly rescanned')
    monkeypatch.setattr(module,'collect',no_repeat)
    assert certify([unit.root],out,workers=1,resume=True)['blocks']==1


def test_resume_rejects_changed_source_head(tmp_path,monkeypatch):
    unit=dataset(tmp_path/'source');out=tmp_path/'release'
    import research.bar_gpt.phase2_v1.release as module
    def interrupt(*args):raise TimeoutError('fixture')
    monkeypatch.setattr(module,'sample_audit',interrupt)
    with pytest.raises(TimeoutError):certify([unit.root],out,workers=1)
    with (unit.root/'ledger-file-v1/HEAD.json').open('a') as f:f.write(' ')
    with pytest.raises(ValueError,match='Source changed'):certify([unit.root],out,workers=1,resume=True)
