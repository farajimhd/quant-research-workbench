import copy
from dataclasses import asdict, replace
import json
import numpy as np
import pytest
import torch
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.run_ranked_teacher_generalization import admit_gate, gate_targets, build_policy, evaluate_probabilities, load_prepared, exact_metrics
from research.rl_trading.v6.run_ranked_teacher_underfit import coverage_report
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.training import train_session
from test_rl_trading_v6_teacher_forecast import fixture


def test_exact_metrics_uses_json_sequence_contract_without_numeric_tolerance():
    actual = {'forecast_targets': (128, 124), 'quality': ({'ENTRY': .01},)}
    saved = json.loads(json.dumps(actual))
    assert exact_metrics(actual, saved)
    changed = copy.deepcopy(saved); changed['quality'][0]['ENTRY'] = np.nextafter(.01, 1.)
    assert not exact_metrics(actual, changed)
    changed = copy.deepcopy(saved); changed['forecast_targets'].reverse()
    assert not exact_metrics(actual, changed)
    with pytest.raises(ValueError): exact_metrics({'loss': float('nan')}, {'loss': float('nan')})


def make_gate(tmp_path):
    root = tmp_path/'gate'; root.mkdir(); source = tmp_path/'source'; source.mkdir()
    (source/'teacher.py').write_text('fixed model/objective')
    (root/'last.pt').write_bytes(b'checkpoint fixture')
    names = ('wait', 'enter_long', 'hold', 'exit_long')
    metrics = dict(action_class_counts={n: 32 for n in names}, action_class_f1={n: 1. for n in names},
        allocation_targets=32, allocation_ratio_mae=.01, action_quality_mae={'ENTRY': .01, 'EXIT': .01},
        forecast_label_metrics=[{'labels': {n: dict(count=2, f1=1.) for n in ('ENTRY', 'WAIT', 'HOLD', 'EXIT')}} for _ in range(5)],
        forecast_quality_mae=[{'ENTRY': .01, 'EXIT': .01} for _ in range(5)])
    manifest = dict(version='rl-v6-ranked-complete-head-underfit-v1', input_population_preserved=True,
        sealed_labels_read=False, workstation_gpu_used=False, source_files_sha256={'teacher.py': file_hash(source/'teacher.py')})
    manifest['hash'] = digest(manifest)
    (root/'manifest.json').write_text(json.dumps(manifest))
    complete = dict(status='completed', passed=True, reload_exact=True, metrics=metrics,
        generalization_evaluated=False, checkpoint_sha256=file_hash(root/'last.pt'))
    (root/'complete.json').write_text(json.dumps(complete))
    return root, source


def test_admission_requires_exact_all_head_underfit_and_source_checkpoint(tmp_path):
    root, source = make_gate(tmp_path)
    assert admit_gate(root, source_dir=source)[1]['passed']
    report = json.loads((root/'complete.json').read_text()); report['metrics']['forecast_quality_mae'][4]['EXIT'] = .03
    (root/'complete.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='underfit gate'): admit_gate(root, source_dir=source)
    report['metrics']['forecast_quality_mae'][4]['EXIT'] = .01
    (root/'complete.json').write_text(json.dumps(report)); (root/'last.pt').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checkpoint changed'): admit_gate(root, source_dir=source)


def test_source_change_and_manifest_tamper_deny_generalization(tmp_path):
    root, source = make_gate(tmp_path); (source/'teacher.py').write_text('new objective')
    with pytest.raises(ValueError, match='source changed'): admit_gate(root, source_dir=source)
    manifest = json.loads((root/'manifest.json').read_text()); manifest['sealed_labels_read'] = True
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='underfit gate'): admit_gate(root, source_dir=source)


def test_replay_targets_bind_exact_identity_clock_position_and_order():
    _, _, labels = fixture(); keys = [(d.close_us, d.episode_uid, False) for d in labels[::2]]
    selected = gate_targets(labels, keys)
    assert [d.close_us for d in selected] == [k[0] for k in keys]
    assert all(d.order_index == 0 for d in selected)
    for bad in (keys+[keys[0]], keys[::-1], [(keys[0][0], 'unknown', False)], [(keys[0][0], keys[0][1], True)]):
        with pytest.raises(ValueError, match='binding changed'): gate_targets(labels, bad)


@pytest.mark.parametrize('device', ['cpu'] + (['cuda'] if torch.cuda.is_available() else []))
def test_real_probability_audit_changes_no_predictions_weights_or_state_steps(device):
    _, session, labels = fixture(device)
    labels = tuple(replace(d, forecast_actions=np.zeros(len(d.forecast_close_us), np.int64)) for d in labels)
    policy = build_policy(MarketAttentionConfig(top_r=1, market_tokens=2, heads=2), torch.device(device), width=16)
    policy.independent_episode_supervision = True
    before = copy.deepcopy(policy.state_dict())
    plain = asdict(train_session(policy, None, session, labels, (), device=torch.device(device),
        evaluation=True, evaluate_train=True, teacher_loss='branch-balanced-v3', regression_weights=(0., 0.)))
    actual, reports, arrays = evaluate_probabilities(policy, session, labels, torch.device(device))
    assert actual == plain and arrays['probability'].shape == (12, 4)
    assert reports['ENTRY']['positive_count'] == 12 and reports['EXIT']['count'] == 0
    np.testing.assert_allclose(arrays['probability'].sum(1), 1., atol=1e-6)
    np.testing.assert_array_equal(arrays['close_us'], [d.close_us for d in labels])
    assert all(torch.equal(value, policy.state_dict()[name]) for name, value in before.items())
    with pytest.raises(ValueError): evaluate_probabilities(policy, replace(session, role='sealed_test'), labels, torch.device(device))
    assert actual == asdict(train_session(policy, None, session, labels, (), device=torch.device(device), evaluation=True, evaluate_train=True,
        teacher_loss='branch-balanced-v3', regression_weights=(0., 0.)))


def test_real_held_exit_probability_branch_uses_saved_conditional_action():
    _, session, labels = fixture()
    held = tuple(replace(d, token=3, held_index=np.array([0], np.int64), held_features=np.zeros((1, 11), np.float32),
        enter_allowed=np.zeros(2, bool), exit_allowed=np.ones(1, bool), stop_allowed=np.zeros(1, bool), target_allowed=np.zeros(1, bool),
        soft_tokens=(3, 6), soft_probabilities=(.9, .1), allocation_ratio_target=None,
        forecast_actions=np.full(len(d.forecast_close_us), 3, np.int64),
        forecast_probabilities=np.tile(np.array([[0., 0., .1, .9]], np.float32), (len(d.forecast_close_us), 1))) for d in labels[1:])
    policy = build_policy(MarketAttentionConfig(top_r=1, market_tokens=2, heads=2), torch.device('cpu'), width=16)
    metrics, reports, arrays = evaluate_probabilities(policy, replace(session, role='development'), held, torch.device('cpu'))
    assert metrics['action_class_counts']['exit_long'] == reports['EXIT']['positive_count'] == 11
    assert reports['ENTRY']['count'] == 0 and arrays['held'].all()
    np.testing.assert_array_equal(arrays['action'], np.full(11, 3))
    np.testing.assert_array_equal(arrays['probability'][:, :2], np.zeros((11, 2)))


def test_prepared_cache_preserves_verified_axis_and_denies_content_or_window_changes(tmp_path):
    _, session, targets = fixture()
    prior = dict(dataset_sha256='labels', market_dataset_sha256='market', bank_certificate_sha256='bank',
        market_certificate_sha256='certificate', context_split_receipt_sha256=None, input_listings=['A', 'B'],
        target_listing_ids=['A'], arguments={'day': '2026-07-31', 'seconds': 12})
    scope = dict(day='2026-07-31', target_ids=['A'], begin_us=0, end_us=12_000_000,
        coverage=coverage_report(targets, 2))
    torch.save(dict(session=session, targets=targets, scope=scope), tmp_path/'prepared-train.pt')
    binding = {k: prior[k] for k in ('dataset_sha256', 'market_dataset_sha256', 'bank_certificate_sha256',
        'market_certificate_sha256', 'context_split_receipt_sha256', 'input_listings')}
    binding.update(day='2026-07-31', seconds=12, target_listing_ids=['A'])
    receipt = dict(version='rl-v6-ranked-verified-train-cache-v1', sha256=file_hash(tmp_path/'prepared-train.pt'), source_binding=binding)
    (tmp_path/'prepared-train.json').write_text(json.dumps(receipt))
    restored, labels, actual_scope = load_prepared(tmp_path, prior)
    assert restored.listings == session.listings and len(labels) == 12 and actual_scope == scope
    np.testing.assert_array_equal(restored.bank.scalar, session.bank.scalar)
    changed = copy.deepcopy(prior); changed['arguments']['seconds'] = 13
    with pytest.raises(ValueError, match='binding changed'): load_prepared(tmp_path, changed)
    with (tmp_path/'prepared-train.pt').open('ab') as stream: stream.write(b'tampered')
    with pytest.raises(ValueError, match='binding changed'): load_prepared(tmp_path, prior)
