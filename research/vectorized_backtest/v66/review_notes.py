"""Human interpretation contract over existing optimizer evidence; no replay."""
import json
from pathlib import Path
from .publication import write_json
from .distributed_assets import sha

VERSION='v66-high-level-review-v2'


def export(out):
    from .genome import Individual
    from .strategy_mix import leaves
    from .review_contract import input_contract
    out=Path(out);meta=json.loads((out/'packet.json').read_bytes());parents=json.loads((out/'parents.json').read_bytes())
    summaries=[]
    for parent in parents:
        member=Individual.restore(parent['payload'])
        summaries.append(dict(serial=parent['serial'],parent_sha256=parent['record']['program_sha256'],
            branches=[dict(index=i,networks={name:dict(nodes=len(n.nodes),threshold=n.threshold,
                size_source=n.size_source,inputs=input_contract(n)) for name,n in b.networks.items()},
                prices=b.payload()['policy']['price_program']) for i,b in enumerate(leaves(member))]))
    write_json(out/'network-summaries.json',summaries)
    write_json(out/'review-notes-template.json',dict(version=VERSION,scope='training',
        source_generation=meta['source_generation'],parents_sha256=sha(out/'parents.json'),
        prior_outcomes='',summary='',limitations=[],
        candidates=[dict(serial=p['serial'],parent_sha256=p['record']['program_sha256'],
            status='not_reviewed',reason='',evidence=[],hypothesis='',expected_change='',tradeoff='',falsification='') for p in parents]))


def validate(out,package,notes):
    out=Path(out);parents=json.loads((out/'parents.json').read_bytes());expected={p['serial']:p for p in parents}
    if (notes['version']!=VERSION or notes['scope']!='training'
        or notes['source_generation']!=package['source_generation']
        or notes['parents_sha256']!=sha(out/'parents.json')):raise ValueError('Review note identity changed')
    rows=notes['candidates'];proposed={p['parent'] for p in package['offspring']}
    if len(rows)!=len(expected) or {r['serial'] for r in rows}!=set(expected):raise ValueError('Selected cohort accounting missing')
    if not notes['summary'].strip() or not notes['prior_outcomes'].strip():raise ValueError('Human findings/prior outcomes required')
    if not proposed<=set(expected):raise ValueError('Unreviewed parent')
    verified={}
    for row in rows:
        if row['parent_sha256']!=expected[row['serial']]['record']['program_sha256']:raise ValueError('Parent changed')
        if row['status'] not in ('reviewed','screened','not_reviewed') or not row['reason'].strip():raise ValueError('Explicit review coverage/reason required')
        for ref in row['evidence']:
            path=(out/ref['path']).resolve()
            if not path.is_relative_to(out.resolve()):raise ValueError('Evidence outside packet')
            if path not in verified:verified[path]=sha(path)
            if verified[path]!=ref['sha256']:raise ValueError('Evidence changed')
        if row['serial'] in proposed:
            if row['status']!='reviewed' or not row['evidence']:raise ValueError('Proposal needs interpreted existing evidence')
            if not all(row[k].strip() for k in ('hypothesis','expected_change','tradeoff','falsification')):
                raise ValueError('Evidence-led proposal plan missing')
    for path,fingerprint in verified.items():
        if sha(path)!=fingerprint:raise ValueError('Evidence changed during qualification')
    return dict(selected=len(rows),reviewed=sum(r['status']=='reviewed' for r in rows),
        screened=sum(r['status']=='screened' for r in rows),not_reviewed=sum(r['status']=='not_reviewed' for r in rows),proposed=len(proposed))
