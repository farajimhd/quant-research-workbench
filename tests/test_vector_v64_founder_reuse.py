import json
import pytest
import numpy as np
from research.vectorized_backtest.v64 import founder_reuse as reuse
from research.vectorized_backtest.v64.distributed_assets import sha


def fixture(tmp_path):
    folder=tmp_path/'phase-2/global/generation-0000'; folder.mkdir(parents=True)
    keys=('execution', 'execution_tail', 'strategy_contract', 'objective', 'score_version',
          'training_sha256', 'validation_sha256', 'asset_sha256', 'training_days',
          'validation_days', 'initialization_probe_sha256', 'seed', 'population', 'initial_multiplier')
    old={k:k for k in keys};old['campaign']=str(tmp_path)
    source=tmp_path/'job.json';source.write_text(json.dumps(old))
    for name in ('training','validation','population'):
        (folder/(name+'.json')).write_text('{}')
    seal={name+'_sha256':sha(folder/(name+'.json')) for name in ('training','validation','population')}
    (folder/'complete.json').write_text(json.dumps(seal))
    job=dict(old,selection_restart=dict(job=str(source),job_sha256=sha(source),seal_sha256=sha(folder/'complete.json')))
    return job,folder


def test_authority_rejects_execution_changes(tmp_path):
    job,folder=fixture(tmp_path)
    assert reuse.authority(job)[1]==folder
    job['execution']='changed'
    with pytest.raises(ValueError,match='financial authority'):reuse.authority(job)


def test_authority_rejects_changed_sealed_results(tmp_path):
    job,folder=fixture(tmp_path)
    (folder/'training.json').write_text('{"changed":true}')
    with pytest.raises(ValueError,match='sealed artifact'):reuse.authority(job)


def test_source_job_cannot_be_rebound_silently(tmp_path):
    job,folder=fixture(tmp_path)
    source=tmp_path/'job.json';source.write_text('{}')
    with pytest.raises(ValueError,match='source job'):reuse.authority(job)


def test_v63_genome_schema_and_program_identity_are_preserved():
    from research.vectorized_backtest.v63.genome import sample
    from research.vectorized_backtest.v63.lineage import program_key as old_key
    from research.vectorized_backtest.v64.genome import Individual
    from research.vectorized_backtest.v64.lineage import program_key
    from research.vectorized_backtest.v64.identity import digest
    for old in sample(np.random.default_rng(2021),4):
        payload=old.payload(); restored=Individual.restore(payload)
        assert digest(restored.payload())==digest(payload)
        assert program_key(restored)==old_key(old)
