import polars as pl
import pytest
from research.rl_trading.v6 import saved_label_audit as audit
from research.rl_trading.v6.price_action_opportunities import calculate, Config
from research.rl_trading.v6.opportunity_dataset import write_json, build_shard
from tests.test_rl_trading_v6_opportunity_dataset import fixture_shard


def test_selected_saved_shard_exact_rows_and_corrupt_receipt(tmp_path,monkeypatch):
    task,day,_=fixture_shard(tmp_path,monkeypatch)
    # Avoid a generated cache outside this test runtime while still checking hashes.
    def verified(path,expected):
        assert audit.file_hash(path)==expected
        return path
    monkeypatch.setattr(audit,'verified_local',verified)
    folder=day/'shards/00000'; receipt_hash=audit.file_hash(folder/'complete.json')
    frames=audit.selected_frames(str(folder),receipt_hash,'identity')
    assert frames['labels'].equals(pl.read_parquet(folder/'labels.parquet'))
    with pytest.raises(ValueError,match='identity'): audit.selected_frames(str(folder),receipt_hash,'absent')
    write_json(folder/'complete.json',{'version':'old'})
    with pytest.raises(ValueError,match='hash'): audit.selected_frames(str(folder),receipt_hash,'new-request')


def test_saved_branch_matches_training_threshold_and_no_fabricated_held_rows(monkeypatch):
    bars=pl.DataFrame(dict(time_us=[1_000_000,2_000_000,3_000_000,4_000_000,5_000_000],
        open=[10.,9.,9.2,9.5,10.],high=[10.,9.,9.2,9.5,10.],low=[10.,9.,9.2,9.5,10.],
        close=[10.,9.,9.2,9.5,10.],macd_line=[-1.,-1.,1.,1.,1.],macd_signal=[0.]*5))
    frames=dict(zip(('labels','episodes','pairs','trades'),calculate(bars)))
    proof=dict(ticker='T',begin_us=1_000_000,finish_us=6_000_000,config={'quality_threshold':.9},dataset_sha256='sha')
    monkeypatch.setattr(audit,'product',lambda *args:(proof,frames))
    held=audit.chart('2026-07-31','identity',view='held')
    for row in held['labels']:
        if row['exit_gain'] is None: assert row['action']=='UNLABELLED' and row['label_value'] is None
        else: assert row['action']==('EXIT' if row['exit_gain']>0 and row['exit_quality']>=.9 else 'HOLD')
    flat=audit.chart('2026-07-31','identity',view='flat')
    assert all(r['action']==('ENTRY' if r['entry_gain']>0 and r['entry_quality']>=.9 else 'WAIT') for r in flat['labels'])
    assert flat['timing']['feature_cutoff'].endswith('exclude target candle')
    combined=audit.chart('2026-07-31','identity')
    assert combined['view']=='combined'
    assert {'ENTRY','EXIT'} <= {r['action'] for r in combined['labels']}
    for row in combined['labels']:
        if row['exit_gain'] is not None and row['exit_gain']>0 and row['exit_quality']>=.9:
            assert row['action']=='EXIT' and row['label_value']==row['exit_quality']
    with pytest.raises(ValueError): audit.chart('2026-07-31','identity',view='unknown')
