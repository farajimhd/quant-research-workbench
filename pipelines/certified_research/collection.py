"""Bounded whole-collection execution over immutable issuer member plans.

Storage adapters must persist the definition before invoking this controller.
Progress is never authority: every member is independently verified again
before a collection root may be published.
"""
from dataclasses import dataclass
from uuid import UUID, uuid5
from .contracts import digest

VERSION = 'complete-issuer-collection-v1'


@dataclass(frozen=True)
class Member:
    dataset_id: str
    issuer_keys: tuple[int, ...]
    plan_hash: str


def member_id(collection_id, partition_hash, index):
    """Stable IDs allow interrupted preparation to recover the same children."""
    return str(uuid5(UUID(collection_id), f'{VERSION}:{partition_hash}:{index}'))


def definition(collection_id, partition, members):
    UUID(collection_id)
    body = dict(partition)
    partition_hash = body.pop('partition_hash')
    if digest(body) != partition_hash:
        raise ValueError('Collection partition fingerprint differs')
    members = tuple(members)
    groups = tuple(tuple(g) for g in partition['groups'])
    if not members or len(members) != len(groups) or len(members) > 16384:
        raise ValueError('Incomplete or oversized collection member list')
    keys = tuple(k for group in groups for k in group)
    if any(not 1 <= len(group) <= 256 or tuple(sorted(set(group))) != group
           or any(type(k) is not int or not 0 < k < 2**64 for k in group) for group in groups):
        raise ValueError('Collection issuer groups exceed the normalized cohort contract')
    if len(keys) != len(set(keys)) or tuple(sorted(keys)) != tuple(partition['issuer_keys']):
        raise ValueError('Collection issuer partition is incomplete or duplicated')
    for index, (group, member) in enumerate(zip(groups, members)):
        if (member.dataset_id != member_id(collection_id, partition_hash, index)
                or member.issuer_keys != group or not group):
            raise ValueError('Collection member differs from frozen issuer partition')
        if len(member.plan_hash) != 64 or any(c not in '0123456789abcdef' for c in member.plan_hash):
            raise ValueError('Collection member plan fingerprint is invalid')
    value = dict(version=VERSION, collection_id=collection_id, partition=partition,
                 members=tuple(dict(dataset_id=m.dataset_id, issuer_keys=m.issuer_keys,
                                    plan_hash=m.plan_hash) for m in members))
    return dict(value, collection_hash=digest(value))


class Controller:
    """Sequential cohorts, concurrent units inside each existing launch.

    `capacity_guard` admits the aggregate retained outputs before any writer.
    `guard` checks the durable definition, ownership and frozen source epoch.
    The caller's seal adapter must insert/read back idempotently in ClickHouse.
    """
    def __init__(self, frozen, load_launch, guard, capacity_guard, publish_seal):
        body = dict(frozen); fingerprint = body.pop('collection_hash')
        rebuilt = definition(body['collection_id'], body['partition'],
                             tuple(Member(m['dataset_id'], tuple(m['issuer_keys']), m['plan_hash'])
                                   for m in body['members']))
        if rebuilt != frozen or digest(body) != fingerprint:
            raise ValueError('Collection definition changed')
        self.frozen = frozen
        self.load_launch = load_launch
        self.guard = guard
        self.capacity_guard = capacity_guard
        self.publish_seal = publish_seal

    def check(self):
        body = dict(self.frozen); fingerprint = body.pop('collection_hash')
        if digest(body) != fingerprint:
            raise ValueError('Collection definition changed during execution')
        self.guard()

    def launch(self, member):
        self.check()
        launch = self.load_launch(member['dataset_id'])
        if (launch.controller.plan_hash != member['plan_hash']
                or tuple(launch.config.issuer_keys) != tuple(member['issuer_keys'])
                or launch.controller.dataset != member['dataset_id']):
            raise ValueError('Loaded collection member differs from frozen plan')
        return launch

    def verify(self):
        roots = []
        for member in self.frozen['members']:
            root = self.launch(member).verify()
            if not isinstance(root, str) or len(root) != 64 or any(c not in '0123456789abcdef' for c in root):
                raise ValueError('Member authority verifier returned an invalid root')
            roots.append((member['dataset_id'], member['plan_hash'], root))
        self.check()
        value = dict(collection_hash=self.frozen['collection_hash'], members=tuple(roots))
        return dict(value, root_hash=digest(value))

    def run(self, *, stop=None, progress=None):
        self.check()
        admission = self.capacity_guard(self.frozen)
        proof = dict(admission); fingerprint = proof.pop('certificate_hash', None)
        if (proof.get('status') != 'admitted'
                or proof.get('collection_hash') != self.frozen['collection_hash']
                or fingerprint != digest(proof)):
            raise ValueError('Aggregate collection capacity admission failed')
        self.check()
        for index, member in enumerate(self.frozen['members']):
            if stop and stop():
                return dict(status='interrupted', completed=index, total=len(self.frozen['members']))
            if progress:
                progress(dict(phase='cohort', active=index + 1, completed=index,
                              total=len(self.frozen['members']), dataset_id=member['dataset_id']))
            result = self.launch(member).run(stop=stop)
            if result.get('status') != 'complete':
                return dict(status=result.get('status', 'failed'), completed=index,
                            total=len(self.frozen['members']), member=member['dataset_id'])
        certificate = self.verify()
        self.check()
        self.publish_seal(certificate)
        self.check()
        return dict(status='complete', completed=len(self.frozen['members']), certificate=certificate)
