"""Off-lane, training-only coverage and checked publication for manual reviews.

Missing telemetry is unknown, never zero. This module does not change selection.
"""
import argparse
import json
import time
from pathlib import Path

from .publication import write_json
from .distributed_assets import sha

VERSION = 'v66-endpoint-review-v1'
CHECKS = ('semantics', 'inputs', 'routing', 'ownership', 'entry_funnel',
          'price_compatibility', 'management_traces', 'ticker_breadth',
          'robustness', 'prior_outcomes')


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
                threshold=network.threshold, size_source=network.size_source)
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
    if not proposed <= set(expected):
        raise ValueError('Unreviewed offspring parent')
    if not proposed and any(c['status']=='untested' for r in rows
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
                if not path.is_relative_to(out.resolve()) or sha(path) != ref['sha256']:
                    raise ValueError('Review evidence missing, outside packet, or changed')
        if row['serial'] in proposed:
            if row['decision'] not in ('repair', 'control') or not original['canonical']:
                raise ValueError('Unqualified parent')
            if any(c['status'] == 'untested' for c in [*row['endpoints'].values(), *row['checks'].values()]):
                raise ValueError('Offspring parent has untested coverage')
            if not all(row[k].strip() for k in ('hypothesis','expected_change','tradeoff','falsification')):
                raise ValueError('Offspring requires falsifiable repair/control plan')
    return dict(reviewed=len(rows), proposed=len(proposed), deferred=sum(r['decision']=='defer' for r in rows))


def publish(out, job):
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
