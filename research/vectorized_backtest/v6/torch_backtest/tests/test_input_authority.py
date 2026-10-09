import json
import pytest
from research.vectorized_backtest.v6.torch_backtest.input_authority import authorize_day
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def test_final_input_requires_bound_audited_freeze(tmp_path):
    def write(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value));return path
    spec={'training':[{'day':'2026-07-30'}],'validation':[{'day':'2026-09-11'}]}
    sessions=write('sessions.json',spec)
    assert authorize_day(sessions,'2026-07-30')==spec['training'][0]
    with pytest.raises(ValueError,match='sealed'):authorize_day(sessions,'2026-09-11')
    identity=write('identity.json',{'sessions':spec,'arguments':{'profile':False}})
    checkpoint=write('checkpoint.json',{'next_generation':32})
    generation=write('generation.json',{'receipts':['certified']})
    freeze=write('frozen_winner.json',{'winner':{'program':'fixed'},'identity_sha256':file_hash(identity)})
    audit=write('audit.json',dict(status='passed',full_budget_verified=True,
        identity_sha256=file_hash(identity),freeze_sha256=file_hash(freeze),
        checkpoint_sha256=file_hash(checkpoint),generation_bindings=[dict(path=str(generation),sha256=file_hash(generation))]))
    assert authorize_day(sessions,'2026-09-11',freeze)==spec['validation'][0]
    generation.write_text('{}')
    with pytest.raises(ValueError,match='generation changed'):authorize_day(sessions,'2026-09-11',freeze)
    checkpoint.write_text('{}')
    with pytest.raises(ValueError,match='full-budget freeze'):authorize_day(sessions,'2026-09-11',freeze)
    with pytest.raises(ValueError,match='absent'):authorize_day(sessions,'2026-09-12',freeze)
