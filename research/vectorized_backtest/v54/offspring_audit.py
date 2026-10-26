"""First-evaluation outcomes; do not count elite survivors as new offspring."""

def summarize(records, panel, generation):
    groups = {}
    for i, record in enumerate(records):
        if record['generation'] != generation:
            continue
        operation = record['operation']
        kind = ('whole:' + record['whole_mode'] if record.get('whole_network')
                else 'mask') if operation.startswith('crossover') else operation
        group = groups.setdefault(kind, dict(evaluated=0, eligible=0,
            profitable_eligible=0, parent_comparisons=0, beat_best_parent_eligible=0,
            beat_best_parent_profitable_eligible=0, below_both_parents_eligible=0))
        eligible = bool(panel['eligible'][i])
        profitable = float(panel['net_pnl'][i]) > 0
        score = float(panel['score'][i])
        group['evaluated'] += 1
        group['eligible'] += eligible
        group['profitable_eligible'] += eligible and profitable
        parents = record.get('parent_objectives', [])
        if parents:
            beats = score > max(parents) + 1e-9
            group['parent_comparisons'] += 1
            group['beat_best_parent_eligible'] += eligible and beats
            group['beat_best_parent_profitable_eligible'] += eligible and profitable and beats
            group['below_both_parents_eligible'] += eligible and score < min(parents) - 1e-9
    return dict(generation=generation, first_evaluation_only=True,
                post_crossover_mutation=False, groups=groups)
