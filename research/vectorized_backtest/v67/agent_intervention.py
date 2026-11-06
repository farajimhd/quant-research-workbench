"""Auditable, training-only manual offspring intake; never changes fitness rules.

Reviewed proposals extend the normal cohort before their first replay. A durable
decision freezes both an empty intake and an accepted package across retries.
"""
import json
from pathlib import Path

from .distributed_assets import sha
from .identity import digest
from .publication import write_json

LEGACY_POLICY = dict(version='training-agent-offspring-v1', interval=10,
              maximum_offspring=64, maximum_per_parent=4, replace='fresh-only')
POLICY = dict(version='training-agent-offspring-v2', interval=5,
              maximum_offspring=256, maximum_per_parent=1, replace='append',
              review_candidates=256)


def check_policy(job):
    if job.get('agent_intervention') is not None:
        raise ValueError('Unknown agent intervention policy')


def policy(job):
    check_policy(job)
    return job.get('agent_intervention') or POLICY


def check_transition(before, after, change):
    """Keep historical introduction receipts valid; pin the one bounded upgrade."""
    if before is None and after in (LEGACY_POLICY, POLICY) and change == after:
        return
    if before == LEGACY_POLICY and after == POLICY and change == dict(before=before, after=after):
        return
    raise ValueError('Unapproved agent intervention transition')


def evidence(root, generation):
    """Explicit allowlist: validation files are never opened."""
    folder = Path(root) / f'generation-{generation:04d}'
    seal = json.loads((folder / 'complete.json').read_bytes())
    names = {'population.json': seal['population_sha256'],
             'training.json': seal['training_sha256'],
             'evaluated-pedigree.json': seal['evaluated_pedigree_sha256'],
             'session-metrics.json': seal['observational_reports']['session-metrics.json']}
    for name, expected in names.items():
        if sha(folder / name) != expected:
            raise ValueError('Training review evidence changed: ' + name)
    return folder, dict(seal_sha256=sha(folder / 'complete.json'), files=names)


def validate_package(root, job, package, generation):
    from .genome import Individual
    from .lineage import program_key
    if set(package) != {'version','scope','review_epoch','source_generation',
                        'training_sha256','evidence','rationale','offspring'}:
        raise ValueError('Unexpected review package fields')
    rules = policy(job); interval = rules['interval']
    source = package['source_generation']
    epoch = package['review_epoch']
    if (package['version'] != rules['version'] or package['scope'] != 'training'
            or type(source) is not int or type(epoch) is not int
            or epoch < interval or epoch % interval or not epoch <= source < epoch + interval
            or not source < generation or generation > epoch + 2*interval
            or package['training_sha256'] != job['training_sha256']
            or not isinstance(package['rationale'], str) or not package['rationale'].strip()
            or len(package['offspring']) > rules['maximum_offspring']):
        raise ValueError('Invalid or stale training review package')
    folder, binding = evidence(root, source)
    if package['evidence'] != binding:
        raise ValueError('Review evidence binding changed')
    records = {r['serial']: r for r in json.loads((folder/'evaluated-pedigree.json').read_bytes())}
    children=[]; parents=[]; counts={}; keys=set()
    for item in package['offspring']:
        if set(item) != {'parent','parent_sha256','payload','hypothesis'} or not item['hypothesis'].strip():
            raise ValueError('Offspring needs an explicit hypothesis')
        parent = records[item['parent']]
        if parent['program_sha256'] != item['parent_sha256']:
            raise ValueError('Review parent identity changed')
        counts[item['parent']] = counts.get(item['parent'], 0) + 1
        if counts[item['parent']] > rules['maximum_per_parent']:
            raise ValueError('Review parent concentration exceeded')
        child = Individual.restore(item['payload']); key = program_key(child)
        from .authority import construct
        if program_key(construct(child)) != key:
            raise ValueError('Offspring violates the shared construction authority')
        if key == parent['program_sha256'] or key in keys or child.case_owner != parent.get('case_owner'):
            raise ValueError('Duplicate offspring or owner changed')
        keys.add(key); children.append(child); parents.append(parent)
    return children, parents


def apply(root, job, generation, members, ids, pedigree, consumed):
    """Run before Snapshot/packing; v1 replaces fresh slots, v2 appends proposals."""
    check_policy(job)
    if not job.get('agent_intervention'):
        return members, ids, consumed, None
    from .lineage import program_key
    root=Path(root); review=root/'agent-reviews'; decisions=review/'decisions'
    decisions.mkdir(parents=True, exist_ok=True)
    decision_path=decisions/f'generation-{generation:04d}.json'
    base=digest(dict(ids=ids.tolist(), keys=[pedigree.records[int(i)]['program_sha256'] for i in ids]))
    if decision_path.exists():
        decision=json.loads(decision_path.read_bytes())
        if decision['base_sha256'] != base:
            raise ValueError('Review retry population changed')
    else:
        pending=[]
        for path in sorted((review/'inbox').glob('*.json')):
            value=json.loads(path.read_bytes())
            if str(value['review_epoch']) not in consumed:
                pending.append((path,value))
        if len(pending)>1:
            raise ValueError('Only one outstanding agent review is allowed')
        decision=dict(base_sha256=base, package=pending[0][1] if pending else None)
        # Validate before freezing; invalid packages fail visibly, never get skipped.
        if decision['package'] is not None:
            validate_package(root,job,decision['package'],generation)
        write_json(decision_path,decision)
    package=decision['package']
    if package is None:return members,ids,consumed,None
    epoch=str(package['review_epoch']); package_hash=digest(package)
    if epoch in consumed:raise ValueError('Agent review already consumed')
    children,parents=validate_package(root,job,package,generation)
    training=json.loads((root/f"generation-{package['source_generation']:04d}"/'training.json').read_bytes())
    scores=dict(zip(training['ids'],training['panel']['score']))
    rules=policy(job)
    append=rules['replace']=='append'
    if append and len(members)!=job['population']:
        raise ValueError('Review append requires the normal successor population')
    slots=(list(range(len(members),len(members)+len(children))) if append else
           [i for i,s in enumerate(ids) if pedigree.records[int(s)]['generation']==generation
            and pedigree.records[int(s)]['operation'].startswith('fresh')])
    if len(slots)<len(children):raise ValueError('Insufficient fresh offspring slots')
    existing={program_key(m) for m in members}
    if any(program_key(child) in existing for child in children):
        raise ValueError('Manual offspring duplicates the current cohort')
    for parent in parents:
        if parent['serial']>=pedigree.next_serial:raise ValueError('Parent serial is outside prior lineage')
        prior=pedigree.records.get(parent['serial'])
        if prior and prior['program_sha256']!=parent['program_sha256']:
            raise ValueError('Imported parent lineage conflict')
        if prior is None:pedigree.records[parent['serial']]=parent
    born=pedigree.birth(children,generation,[(p['serial'],-1) for p in parents],
                        ['agent_mutation']*len(children))
    members=list(members);ids=ids.copy();replaced=[]
    if append:
        import numpy as np
        members.extend(children);ids=np.concatenate((ids,born))
    for slot,child,serial,item in zip(slots,children,born,package['offspring']):
        if not append:
            replaced.append(int(ids[slot]));members[slot]=child;ids[slot]=serial
        pedigree.records[int(serial)].update(agent_review_sha256=package_hash,
            review_epoch=int(epoch),hypothesis=item['hypothesis'],fitness_evidence='unreplayed proposal',
            parent_objectives=[float(scores[item['parent']])],
            agent_control=item['hypothesis'].startswith('control:'))
    consumed=dict(consumed,**{epoch:package_hash})
    report=dict(package_sha256=package_hash,review_epoch=int(epoch),source_generation=package['source_generation'],
                decision_sha256=sha(decision_path),new_ids=born.tolist(),replaced_ids=replaced,
                scope='training',admission='experimental; normal full replay and selection')
    if append:
        report.update(mode='append',base_population=job['population'],replay_population=len(members),
                      successor_population=job['population'])
    return members,ids,consumed,report
