"""Freeze bounded sealed optimizer evidence without opening market/feature data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re


def _bytes_hash(raw):
    return sha256(raw).hexdigest()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return _bytes_hash(_json_bytes(value))


def _inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Evidence path escapes root')
    return path


def _read(path, maximum):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Evidence file exceeds bound')
    return raw


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _index(rows, population_ids=None):
    result = {}
    seen = set()
    for row in rows:
        serial, candidate = row['serial'], row['candidate']
        if (type(serial) is not int or type(candidate) is not int or candidate < 1
                or serial in result or candidate in seen):
            raise ValueError('Duplicate or invalid ranking identity')
        if population_ids is not None and (candidate > len(population_ids)
                or population_ids[candidate - 1] != serial):
            raise ValueError('Ranking differs from population serial')
        result[serial] = row
        seen.add(candidate)
    return result


def _collect_candidate_evidence(campaign, generation, serials, *,
                              workers=2, max_file_bytes=128 * 1024 * 1024,
                              max_total_bytes=1024 * 1024 * 1024):
    """Copy exact source bytes and publish manifest last in an exclusive folder.

    The manifest is the completion marker. A failed copy may leave an incomplete
    folder, which must never be reused. Hashes make subsequent changes detectable;
    filesystem access control is the caller's retention responsibility.
    """
    if (type(generation) is not int or generation < 1 or not 1 <= workers <= 8
            or max_file_bytes < 1 or max_total_bytes < 1):
        raise ValueError('Invalid bounded freeze parameters')
    selected = tuple(serials)
    if not selected or len(set(selected)) != len(selected) or any(type(s) is not int or s < 1 for s in selected):
        raise ValueError('Explicit unique positive candidate serials required')
    source = Path(campaign).resolve(strict=True)
    blobs = {}
    def load(relative):
        if relative not in blobs:
            raw = _read(_inside(source, relative), max_file_bytes)
            if sum(map(len, blobs.values())) + len(raw) > max_total_bytes:
                raise ValueError('Evidence total byte bound exceeded')
            blobs[relative] = raw
        return _decode(blobs[relative])
    folder = f'generation-{generation:04d}'
    metadata = {}
    for name in ('active.json', 'exit.json'):
        if _inside(source, name).exists():
            metadata[name] = load(name)
    provenance = _provenance(metadata, generation)
    datasets = {role: load(f'{role}.json') for role in ('training', 'validation')}
    day_sets = {}
    for role, dataset in datasets.items():
        days = dataset['days']
        if not isinstance(days, list) or not days or any(not isinstance(day, str) for day in days):
            raise ValueError('Explicit session day list required')
        if any(date.fromisoformat(day).isoformat() != day for day in days) or days != sorted(set(days)):
            raise ValueError('Session dates must be valid, unique and ordered')
        day_sets[role] = days
    if max(day_sets['training']) >= min(day_sets['validation']):
        raise ValueError('Training and validation must be chronologically disjoint')
    seals = {role: load(f'{role}/{folder}/complete.json') for role in datasets}
    population = load(f'training/{folder}/population.json')
    payloads, ids = population['population'], population['ids']
    if len(payloads) != len(ids) or len(set(ids)) != len(ids) or not ids:
        raise ValueError('Invalid training population identities')
    if seals['training']['population_sha256'] != _digest(payloads):
        raise ValueError('Training population digest differs')
    rankings = {role: load(f'{role}/{folder}/ranking.json') for role in datasets}
    train = _index(rankings['training'], ids)
    valid = _index(rankings['validation'])
    if not set(selected) <= set(train) & set(valid):
        raise ValueError('Selected candidates must occur in both rankings')
    ordered_validation = sorted(valid.values(), key=lambda row: row['candidate'])
    if [row['candidate'] for row in ordered_validation] != list(range(1, len(valid) + 1)):
        raise ValueError('Validation candidate indices must be contiguous')
    if not set(valid) <= set(train):
        raise ValueError('Validation candidate absent from training ranking')
    subset = [payloads[train[row['serial']]['candidate'] - 1] for row in ordered_validation]
    if seals['validation']['population_sha256'] != _digest(subset):
        raise ValueError('Validation ordered subset digest differs')
    jobs = []
    for role, seal in seals.items():
        days = datasets[role]['days']
        if (len(set(days)) != len(days) or not days
                or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in days)
                or set(days) != set(seal['session_receipts'])
                or seal['validation_feedback'] is not False):
            raise ValueError('Incomplete or unsafe sealed session set')
        if role == 'training' and seal['training_days'] != days:
            raise ValueError('Training seal day order differs')
        if role == 'validation' and seal['display_only'] is not True:
            raise ValueError('Validation must be display only')
        panel = f'{role}/{folder}/session-panel.npz'
        raw = _read(_inside(source, panel), max_file_bytes)
        if _bytes_hash(raw) != seal['session_panel_sha256']:
            raise ValueError('Sealed session panel changed')
        blobs[panel] = raw
        jobs.extend((role, day, f'{role}/{folder}/{day}/receipt.json') for day in days)
    # Bound aggregate memory before concurrent reads, then verify actual sizes.
    if sum(map(len, blobs.values())) + sum(_inside(source, job[2]).stat().st_size for job in jobs) > max_total_bytes:
        raise ValueError('Evidence total byte bound exceeded')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for job, raw in zip(jobs, pool.map(lambda job: _read(_inside(source, job[2]), max_file_bytes), jobs)):
            blobs[job[2]] = raw
    if sum(map(len, blobs.values())) > max_total_bytes:
        raise ValueError('Evidence total byte bound exceeded')
    sessions = {serial: {'training': [], 'validation': []} for serial in selected}
    execution = None
    for role, day, relative in jobs:
        receipt = _decode(blobs[relative])
        if (_bytes_hash(blobs[relative]) != seals[role]['session_receipts'][day]
                or receipt['population_sha256'] != seals[role]['population_sha256']
                or receipt['full_session'] is not True or receipt['split'] != role or receipt['day'] != day
                or receipt['validation_opened'] is not (role == 'validation')):
            raise ValueError('Receipt differs from sealed full-session identity')
        if execution is None:
            execution = receipt['execution']
        if receipt['execution'] != execution:
            raise ValueError('Execution configuration differs across receipts')
        count = len(ids) if role == 'training' else len(valid)
        metrics = receipt['metrics']
        if not metrics or any(not isinstance(values, list) or len(values) != count for values in metrics.values()):
            raise ValueError('Metrics population dimension differs')
        for serial in selected:
            row = (train if role == 'training' else valid)[serial]
            index = row['candidate'] - 1
            extracted = {name: values[index] for name, values in metrics.items()}
            episodes = [value for name, value in extracted.items() if name.startswith('episode_')]
            if episodes and (any(not isinstance(value, list) for value in episodes)
                             or len({len(value) for value in episodes}) != 1):
                raise ValueError('Ragged episode columns disagree within candidate')
            sessions[serial][role].append({'day': day, 'receipt_sha256': _bytes_hash(blobs[relative]), 'metrics': extracted})
    hashes = {relative: _bytes_hash(raw) for relative, raw in sorted(blobs.items())}
    evidence_hash = _digest(hashes)
    envelopes = []
    for serial in selected:
        index = train[serial]['candidate']
        program = payloads[index - 1]
        envelopes.append(dict(version='v19', generation=generation, serial=serial, candidate=index,
            program=program, execution=execution, universe=dict(version='v6-volume-market-blocks-v1',
                window=30, top_n=10, minimum_close=.8, maximum_close=50.),
            program_hash=_digest(program), evidence_hash=evidence_hash))
    manifest = dict(version='optimized-candidate-evidence-v1', generation=generation,
        serials=list(selected), sources=hashes, evidence_hash=evidence_hash,
        candidate_envelopes=envelopes, sessions=sessions, provenance=provenance, complete=True)
    manifest_raw = _json_bytes(manifest)
    # Recheck every source before committing the immutable snapshot.
    for relative, raw in blobs.items():
        if _read(_inside(source, relative), max_file_bytes) != raw:
            raise ValueError('Source changed during evidence freeze')
    return manifest, blobs


def freeze_candidate_evidence(campaign, generation, serials, output, *, runtime_root,
                              workers=2, max_file_bytes=128 * 1024 * 1024,
                              max_total_bytes=1024 * 1024 * 1024):
    """Publish verified source bytes to an exclusive runtime folder, manifest last."""
    source = Path(campaign).resolve(strict=True)
    runtime = Path(runtime_root).resolve(strict=True)
    target = Path(output).resolve()
    if not target.is_relative_to(runtime) or target == runtime or target.is_relative_to(source):
        raise ValueError("Output must be an exclusive runtime directory outside campaign")
    manifest, blobs = _collect_candidate_evidence(source, generation, serials, workers=workers,
        max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes)
    manifest_raw = _json_bytes(manifest)
    target.mkdir(parents=True, exist_ok=False)
    for relative, raw in blobs.items():
        destination = _inside(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(raw)
    with (target / 'manifest.sha256').open('x', encoding='ascii') as stream:
        stream.write(_bytes_hash(manifest_raw) + '\n')
    with (target / 'manifest.json').open('xb') as stream:
        stream.write(manifest_raw)
    return manifest


def select_audit_shortlist(candidates, *, count):
    """Deterministic structural diversity selection; never ranks financial profit.

    Each record supplies serial, effective_program_policy_hash (computed from
    the compiled reachable program and policy), and stage_capability_signature.
    Distinctness is structural evidence, not proof of economic generalization.
    """
    if type(count) is not int or count < 1:
        raise ValueError('Explicit positive shortlist count required')
    records = sorted(candidates, key=lambda row: row['serial'])
    if len({row['serial'] for row in records}) != len(records):
        raise ValueError('Duplicate shortlist serial')
    result, seen = [], set()
    for row in records:
        identity = row['effective_program_policy_hash']
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
            raise ValueError('Effective program and policy SHA256 required')
        signature = _digest(row['stage_capability_signature'])
        key = (identity, signature)
        if key not in seen:
            result.append(row)
            seen.add(key)
    if len(result) < count:
        raise ValueError('Insufficient structurally distinct audit candidates')
    return tuple(result[:count])


def _provenance(metadata, generation):
    active = metadata.get('active.json')
    exit_record = metadata.get('exit.json')
    result = dict(status='provenance_unverified', source_tree_verified=False,
                  active_present=active is not None, exit_present=exit_record is not None,
                  source_sha256=None, declared_final_generation=None)
    if active is None:
        return result
    identity = active.get('source_sha256')
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('Active metadata requires source SHA256')
    if active.get('validation_feedback') is not False:
        raise ValueError('Active metadata permits validation feedback')
    args = active.get('arguments', [])
    if not isinstance(args, list) or args.count('--generations') != 1:
        raise ValueError('Active metadata requires final generation argument')
    try:
        final = int(args[args.index('--generations') + 1])
    except (IndexError, ValueError, TypeError) as error:
        raise ValueError('Invalid final generation argument') from error
    if not 1 <= generation <= final:
        raise ValueError('Selected generation exceeds declared campaign')
    if generation == final and (exit_record is None or exit_record.get('exit_code') != 0):
        raise ValueError('Final generation requires successful campaign exit')
    result.update(status='source_metadata_recorded' if exit_record is not None else 'provenance_unverified', source_sha256=identity,
                  declared_final_generation=final)
    return result


def read_frozen_candidate_evidence(root, *, workers=2, max_files=10000,
                                   max_file_bytes=128 * 1024 * 1024,
                                   max_total_bytes=1024 * 1024 * 1024):
    """Verify detached snapshot bytes and recompute every derived envelope/session.

    Recorded optimizer source SHA is provenance evidence, not verification of
    the optimizer code tree. No active campaign or market input is consulted.
    """
    if not 1 <= workers <= 8 or max_files < 1 or max_file_bytes < 1 or max_total_bytes < 1:
        raise ValueError('Invalid frozen reader bounds')
    root = Path(root).resolve(strict=True)
    raw = _read(_inside(root, 'manifest.json'), max_file_bytes)
    expected = _read(_inside(root, 'manifest.sha256'), 66).decode('ascii').strip()
    if _bytes_hash(raw) != expected:
        raise ValueError('Frozen manifest SHA differs')
    manifest = _decode(raw)
    required = {'version', 'generation', 'serials', 'sources', 'evidence_hash',
                'candidate_envelopes', 'sessions', 'provenance', 'complete'}
    if (set(manifest) != required or manifest['version'] != 'optimized-candidate-evidence-v1'
            or manifest['complete'] is not True):
        raise ValueError('Unsupported or incomplete frozen manifest')
    sources = manifest['sources']
    if not isinstance(sources, dict) or not sources or len(sources) > max_files:
        raise ValueError('Frozen source count exceeds bound')
    if _digest(sources) != manifest['evidence_hash']:
        raise ValueError('Frozen evidence source digest differs')
    paths = []
    for relative, identity in sources.items():
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or '..' in Path(relative).parts or relative in ('manifest.json', 'manifest.sha256')
                or not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity)):
            raise ValueError('Unsafe frozen source identity')
        paths.append(_inside(root, relative))
    if sum(path.stat().st_size for path in paths) > max_total_bytes:
        raise ValueError('Frozen source total byte bound exceeded')
    def verify(item):
        relative, path = item
        content = _read(path, max_file_bytes)
        if _bytes_hash(content) != sources[relative]:
            raise ValueError('Frozen source file changed')
        return len(content)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        sizes = list(pool.map(verify, zip(sources, paths)))
    if sum(sizes) > max_total_bytes:
        raise ValueError('Frozen source total byte bound exceeded')
    recomputed, _ = _collect_candidate_evidence(root, manifest['generation'], manifest['serials'],
        workers=workers, max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes)
    if _json_bytes(recomputed) != _json_bytes(manifest):
        raise ValueError('Frozen manifest differs from its sealed source evidence')
    return manifest


def verify_v19_source_tree(code_root, expected_sha256):
    """Reproduce archived V19 source_hash without importing or executing code."""
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('Expected source SHA256 required')
    code_root = Path(code_root).resolve(strict=True)
    source = _inside(code_root, 'research/vectorized_backtest')
    digest = sha256()
    count = 0
    for version in ('v6', 'v7', 'v19'):
        folder = _inside(source, version)
        paths = sorted(folder.rglob('*.py'))
        if not paths:
            raise ValueError('Incomplete V19 source tree')
        for path in paths:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(folder):
                raise ValueError('Source tree path escaped version directory')
            digest.update(path.relative_to(source).as_posix().encode())
            digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
            count += 1
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError('Optimizer source tree SHA256 differs from recorded provenance')
    return dict(version='v19-source-tree-verification-v1', source_sha256=actual,
                expected_sha256=expected_sha256, files=count, verified=True)
