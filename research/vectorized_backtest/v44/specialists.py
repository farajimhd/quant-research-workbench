"""Bounded durable specialist archive and training-only breeding admission."""
import numpy as np
from research.vectorized_backtest.v42.genome import Individual
from research.vectorized_backtest.v42.lineage import program_key


def breeding_merit(broad, specialist, ids):
    """Best rank across two equal routes; no dollar/unit mixing."""
    result = np.zeros(len(ids))
    for values in (broad, specialist):
        values = np.asarray(values)
        if values.shape != result.shape or not np.isfinite(values).all():
            raise ValueError('Aligned finite dual scores required')
        order = np.lexsort((ids, -values))
        rank = np.empty(len(ids)); rank[order] = np.arange(len(ids), 0, -1)
        result = np.maximum(result, rank)
    return result


def specialist_order(values, targets, ids, valid):
    groups = {}
    for i in np.lexsort((ids, -np.asarray(values))):
        if valid[i] and values[i] > 0:
            groups.setdefault(str(targets[i]), []).append(int(i))
    return [rows[level] for level in range(max(map(len, groups.values()), default=0))
            for rows in groups.values() if level < len(rows)]


def update(previous, members, ids, pedigree, metrics, panel, generation,
           capacity=256, per_case=2):
    if not 1 <= capacity <= 256 or not 1 <= per_case <= 2:
        raise ValueError('Bounded specialist archive required')
    candidates = {r['serial']: r for r in previous}
    for i in np.flatnonzero(panel['eligible'][0]):
        if panel['specialist'][i] <= 0 or panel['utility'][:, i].max() <= 0:
            continue
        candidates[int(ids[i])] = dict(serial=int(ids[i]), payload=members[i].payload(),
            pedigree=dict(pedigree.records[int(ids[i])]), generation=generation,
            metrics={k: [v[i]] for k, v in metrics.items()},
            case_scores=panel['case_specialist'][:, i].tolist(),
            case_utility=panel['utility'][:, i].tolist(), source_generation=generation)
    groups = []
    for case, name in enumerate(panel['case_ids']):
        eligible = [r for r in candidates.values()
                    if r['case_scores'][case] > 0 and r['case_utility'][case] > 0]
        eligible.sort(key=lambda r: (-r['case_scores'][case], r['serial']))
        unique, seen = [], set()
        for row in eligible:
            key = program_key(Individual.restore(row['payload']))
            if key not in seen:
                unique.append(row); seen.add(key)
            if len(unique) == per_case:
                break
        if unique:
            groups.append((name, unique, case))
    groups.sort(key=lambda pair: (-pair[1][0]['case_scores'][pair[2]], pair[0]))
    selected, niches = {}, []
    for level in range(per_case):
        for case, rows, _ in groups:
            if level >= len(rows):
                continue
            row = rows[level]
            if row['serial'] not in selected and len(selected) >= capacity:
                continue
            selected[row['serial']] = row
            niches.append(dict(case=case, serial=row['serial'], rank=level + 1))
    return list(selected.values()), niches


def admit(members, ids, pedigree, metrics, archive):
    """Reuse exact same-session metrics for archived parents, never new children."""
    members = list(members); ids = list(map(int, ids)); known = set(ids)
    merged = {k: list(v) for k, v in metrics.items()}
    for row in archive:
        serial = row['serial']
        if serial in known:
            continue
        members.append(Individual.restore(row['payload'])); ids.append(serial); known.add(serial)
        pedigree.records[serial] = dict(row['pedigree'])
        if set(row['metrics']) != set(merged):
            raise ValueError('Archived training metric schema changed')
        for k in merged:
            merged[k].extend(row['metrics'][k])
    return members, np.asarray(ids, dtype=np.int64), merged
