from types import SimpleNamespace
from uuid import uuid4
import pytest
from pipelines.certified_research.collection import Controller, Member, definition, member_id
from pipelines.certified_research.contracts import digest


def fixture():
    partition = dict(groups=((1, 2), (3,)), issuer_keys=(1, 2, 3))
    partition['partition_hash'] = digest(partition)
    collection = str(uuid4())
    members = tuple(Member(member_id(collection, partition['partition_hash'], i), group, str(i + 1) * 64)
                    for i, group in enumerate(partition['groups']))
    frozen = definition(collection, partition, members)
    calls = []; seals = []
    launches = {}
    for member in members:
        def run(stop=None, dataset=member.dataset_id):
            calls.append(('run', dataset)); return dict(status='complete')
        def verify(dataset=member.dataset_id):
            calls.append(('verify', dataset)); return 'a' * 64
        launches[member.dataset_id] = SimpleNamespace(
            controller=SimpleNamespace(plan_hash=member.plan_hash, dataset=member.dataset_id),
            config=SimpleNamespace(issuer_keys=member.issuer_keys), run=run, verify=verify)
    def capacity(value):
        proof = dict(status='admitted', collection_hash=value['collection_hash'])
        return dict(proof, certificate_hash=digest(proof))
    controller = Controller(frozen, launches.__getitem__, lambda: None, capacity, seals.append)
    return controller, launches, calls, seals


def test_all_members_reverified_before_seal_and_resume_rechecks():
    controller, launches, calls, seals = fixture()
    assert controller.run()['status'] == 'complete'
    assert [c[0] for c in calls] == ['run', 'run', 'verify', 'verify']
    assert len(seals) == 1
    controller.run()
    assert len(calls) == 8  # Child controllers own certified idempotent reuse.


def test_failure_or_stop_never_seals_partial_collection():
    controller, launches, calls, seals = fixture()
    assert controller.run(stop=lambda: True)['status'] == 'interrupted'
    assert not calls and not seals
    list(launches.values())[1].run = lambda **kwargs: dict(status='blocked')
    assert controller.run()['status'] == 'blocked'
    assert not seals and all(c[0] == 'run' for c in calls)


def test_changed_member_or_final_authority_blocks_seal():
    controller, launches, calls, seals = fixture()
    list(launches.values())[1].config.issuer_keys = (4,)
    with pytest.raises(ValueError, match='frozen plan'): controller.run()
    assert not seals
    controller, launches, calls, seals = fixture()
    list(launches.values())[1].verify = lambda: 'bad'
    with pytest.raises(ValueError, match='invalid root'): controller.run()
    assert not seals


def test_aggregate_capacity_required_before_any_member_writer():
    controller, launches, calls, seals = fixture()
    controller.capacity_guard = lambda frozen: dict(status='admitted', certificate_hash='a' * 64)
    with pytest.raises(ValueError, match='capacity'): controller.run()
    assert not calls and not seals


def test_tampered_definition_rejected_before_execution():
    controller, launches, calls, seals = fixture()
    changed = dict(controller.frozen, collection_hash='b' * 64)
    with pytest.raises(ValueError):
        Controller(changed, launches.__getitem__, lambda: None, lambda _: {}, seals.append)
