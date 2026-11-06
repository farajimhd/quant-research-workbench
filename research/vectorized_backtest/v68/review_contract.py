"""Off-lane, training-only coverage and checked publication for manual reviews.

Missing telemetry is unknown, never zero. This module does not change selection.
"""
import argparse
import json
import time
from pathlib import Path

from .publication import write_json
from .distributed_assets import sha

VERSION = 'v67-endpoint-review-v1'
CHECKS = ('semantics', 'inputs', 'routing', 'ownership', 'entry_funnel',
          'price_compatibility', 'management_traces', 'ticker_breadth',
          'robustness', 'prior_outcomes')


def input_contract(network):
    from .genome import CATALOG,MARKET_INPUTS,INPUTS
    rows=[]
    for source in sorted({s for n in network.nodes for s in n.sources if s<INPUTS}):
        if source>=MARKET_INPUTS:
            rows.append(dict(source=source,kind='reserved_state',index=source-MARKET_INPUTS));continue
        feature=CATALOG[source%len(CATALOG)]
        rows.append(dict(source=source,name=feature.name,unit=feature.unit,
                         lower=feature.lower,upper=feature.upper,mask=feature.mask,
                         channel=('absolute_transformed','cross_sectional_relative','availability')[source//len(CATALOG)]))
    return rows


def diagnose(parent):
    from .genome import Individual
    from .strategy_mix import leaves
    from .authority import construct
    from .lineage import program_key
    member = Individual.restore(parent['payload'])
    canonical = program_key(construct(member)) == program_key(member)
    endpoints = {}
    for i, branch in enumerate(leaves(member)):
        for name, network in branch.networks.items():
            endpoints[f'branch.{i}.network.{name}'] = dict(
                status='untested', evidence=[], reason='', nodes=len(network.nodes),
                threshold=network.threshold, size_source=network.size_source,
                inputs=input_contract(network))
        for name, expression in branch.payload()['policy']['price_program'].items():
            endpoints[f'branch.{i}.price.{name}'] = dict(
                status='untested', evidence=[], reason='', expression=expression)
    sessions = parent['sessions']
    traded = sum(s['closed_positions'] > 0 for s in sessions.values())
    profitable = sum(s['net_pnl'] > 0 for s in sessions.values())
    closes = sum(s['closed_positions'] for s in sessions.values())
    net = sum(s['net_pnl'] for s in sessions.values())
    counters = sorted({k for s in sessions.values() for k in s if k.startswith('admission_')})
    return dict(serial=parent['serial'], parent_sha256=parent['record']['program_sha256'],
        canonical=canonical, endpoints=endpoints,
        checks={k:dict(status='untested', evidence=[], reason='') for k in CHECKS},
        sessions=sessions, breadth=dict(all_sessions=len(sessions), traded_sessions=traded,
            profitable_sessions=profitable, profitable_all_fraction=profitable/len(sessions),
            profitable_traded_fraction=profitable/traded if traded else None,
            win_rate=sum(s['winning_positions'] for s in sessions.values())/closes if closes else None,
            net=net, closes=closes, without_best=net-max(s['net_pnl'] for s in sessions.values()),
            ticker_coverage=None, majority_profitable_sessions=profitable > len(sessions)/2),
        funnel={k:sum(s[k] for s in sessions.values()) if all(k in s for s in sessions.values()) else None for k in counters},
        accounting_stress={str(cost):net-cost*closes for cost in (1,5)},
        limitations=['Ticker coverage and pre-order rejection reasons require targeted traces.',
                      'Admission counters can overlap and repeat across clocks; do not sum as unique signals.',
                      'Accounting stress holds trades fixed; it is not execution replay.'],
        decision='incomplete', hypothesis='', expected_change='', tradeoff='', falsification='')


def export_coverage(out):
    out = Path(out)
    packet = json.loads((out/'packet.json').read_bytes())
    parents = json.loads((out/'parents.json').read_bytes())
    result = dict(version=VERSION, scope='training', source_generation=packet['source_generation'],
                  evidence=packet['evidence'], parents_sha256=sha(out/'parents.json'),
                  candidates=[diagnose(p) for p in parents])
    write_json(out/'endpoint-review-template.json', result)
    return result


def validate_coverage(out, package, review):
    """Require explicit outcomes for every endpoint, including honest deferrals."""
    out = Path(out)
    parents = json.loads((out/'parents.json').read_bytes())
    expected = {p['serial']:diagnose(p) for p in parents}
    if (review['version'] != VERSION or review['scope'] != 'training'
            or review['source_generation'] != package['source_generation']
            or review['evidence'] != package['evidence']
            or review['parents_sha256'] != sha(out/'parents.json')):
        raise ValueError('Review identity or training evidence changed')
    rows = review['candidates']
    if len(rows) != len(expected) or {r['serial'] for r in rows} != set(expected):
        raise ValueError('Every selected parent requires one review')
    proposed = {x['parent'] for x in package['offspring']}
    verified_files = {}
    if not proposed <= set(expected):
        raise ValueError('Unreviewed offspring parent')
    if not proposed and any(c['status']!='tested' for r in rows
                            for c in [*r['endpoints'].values(),*r['checks'].values()]):
        raise ValueError('Investigation unfinished: persist progress and resume; no empty intake')
    for row in rows:
        original = expected[row['serial']]
        if row['parent_sha256'] != original['parent_sha256']:
            raise ValueError('Parent identity changed')
        if set(row['endpoints']) != set(original['endpoints']) or set(row['checks']) != set(CHECKS):
            raise ValueError('Endpoint/check coverage missing')
        if row['decision'] not in ('repair', 'control', 'defer', 'reject'):
            raise ValueError('Candidate investigation incomplete')
        for check in [*row['endpoints'].values(), *row['checks'].values()]:
            if check['status'] not in ('tested', 'untested', 'not_applicable') or not check['reason'].strip():
                raise ValueError('Every endpoint needs a result or explicit limitation')
            if check['status'] == 'tested' and not check['evidence']:
                raise ValueError('Tested claim needs evidence')
            for ref in check['evidence']:
                path = (out/ref['path']).resolve()
                if not path.is_relative_to(out.resolve()):
                    raise ValueError('Review evidence missing, outside packet, or changed')
                if path not in verified_files:verified_files[path] = sha(path)
                if verified_files[path] != ref['sha256']:
                    raise ValueError('Review evidence missing, outside packet, or changed')
        if row['serial'] in proposed:
            if row['decision'] not in ('repair', 'control') or not original['canonical']:
                raise ValueError('Unqualified parent')
            if any(c['status'] != 'tested' for c in [*row['endpoints'].values(), *row['checks'].values()]):
                raise ValueError('Offspring parent has untested coverage: every declared endpoint/check needs tested evidence, including inactive endpoints')
            if not all(row[k].strip() for k in ('hypothesis','expected_change','tradeoff','falsification')):
                raise ValueError('Offspring requires falsifiable repair/control plan')
    # Evidence can be shared by thousands of endpoints. Hash each distinct file
    # once, then recheck the complete set to reject changes during validation.
    for path, fingerprint in verified_files.items():
        if sha(path) != fingerprint:raise ValueError('Review evidence changed during validation')
    return dict(reviewed=len(rows), proposed=len(proposed), deferred=sum(r['decision']=='defer' for r in rows))


def publish(out, job):
    from .review_window import enabled
    if enabled(job):return publish_window(out,job)
    from .agent_intervention import validate_package
    out = Path(out); root = Path(job['campaign'])/'phase-2/global'
    package = json.loads((out/'package.json').read_bytes())
    review = json.loads((out/'endpoint-review.json').read_bytes())
    summary = validate_coverage(out, package, review)
    state = json.loads((root/'checkpoint.json').read_bytes())['state']
    consumed = state.get('agent_reviews', {})
    inbox = root/'agent-reviews/inbox'; inbox.mkdir(parents=True, exist_ok=True)
    if str(package['review_epoch']) in consumed or any(
            str(json.loads(p.read_bytes())['review_epoch']) not in consumed for p in inbox.glob('*.json')):
        raise ValueError('Review consumed or intake already pending')
    validate_package(root, job, package, state['completed']+2)
    receipt = dict(**summary, version=VERSION, review_sha256=sha(out/'endpoint-review.json'),
                   package_sha256=sha(out/'package.json'), checked_epoch=time.time())
    write_json(out/'review-qualification.json', receipt)
    write_json(inbox/f"epoch-{package['review_epoch']:04d}.json", package)
    return receipt


def publish_window(out,job):
    from .review_window import locked
    from .review_notes import validate,VERSION as NOTE_VERSION
    from .agent_intervention import validate_package
    out=Path(out);root=Path(job['campaign'])/'phase-2/global';review=root/'agent-reviews'
    with locked(review):
        window=json.loads((out/'window.json').read_bytes())
        package=json.loads((out/'package.json').read_bytes())
        notes=json.loads((out/'review-notes.json').read_bytes())
        if (window['status']!='reviewing' or time.time()>=window['deadline_epoch']
            or package['review_epoch']!=window['review_epoch']
            or package['source_generation']!=window['source_generation']):raise ValueError('Review window closed or source changed')
        state=json.loads((root/'checkpoint.json').read_bytes())['state']
        if state['completed']!=window['source_generation']:raise ValueError('Optimizer no longer at reviewed boundary')
        inbox=review/'inbox';inbox.mkdir(exist_ok=True)
        if str(package['review_epoch']) in state.get('agent_reviews',{}) or any(
            str(json.loads(p.read_bytes())['review_epoch']) not in state.get('agent_reviews',{}) for p in inbox.glob('*.json')):
            raise ValueError('Review consumed or another intake pending')
        summary=validate(out,package,notes)
        # Reuse normal authority; no parent replay, synthetic campaign or extra gate.
        validate_package(root,job,package,state['completed']+1)
        if time.time()>=window['deadline_epoch']:raise ValueError('Review deadline reached during qualification')
        receipt=dict(**summary,version=NOTE_VERSION,review_sha256=sha(out/'review-notes.json'),
            package_sha256=sha(out/'package.json'),checked_epoch=time.time(),evaluation='normal next-generation replay')
        write_json(out/'review-qualification.json',receipt)
        write_json(inbox/f"epoch-{package['review_epoch']:04d}.json",package)
        window.update(status='submitted' if package['offspring'] else 'no_proposals',closed_epoch=time.time(),
            proposed=len(package['offspring']),reviewed=summary['reviewed'],screened=summary['screened'],reason=notes['summary'])
        write_json(out/'window.json',window);write_json(review/'active-window.json',dict(window,packet=str(out)))
        return receipt


if __name__ == '__main__':
    import os
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--job')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.publish:
        if not args.job:parser.error('--publish requires --job')
        print(json.dumps(publish(args.out, json.loads(Path(args.job).read_bytes()))))
    else:
        result = export_coverage(args.out)
        print(json.dumps(dict(candidates=len(result['candidates']), version=VERSION)))
