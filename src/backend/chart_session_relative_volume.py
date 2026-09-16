"""Selection-only chart RVOL using certified full-session volume prefixes."""
from datetime import datetime
from math import floor, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from src.backend.qmd_gateway_client import qmd_history_post_json
from src.backend.session_relative_volume import BaselineStore

FIELD = 'session_relative_volume'
NY = ZoneInfo('America/New_York')


def _clock(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Chart RVOL requires timezone-aware timestamps')
    return stamp


def _closed_rows(payload, as_of):
    rows = []
    for bar in payload.get('history', []):
        if bar.get('is_closed') is False:
            continue
        start, end = _clock(bar['bar_start']), _clock(bar['bar_end'])
        if start < end <= as_of:
            rows.append((bar, end))
    return rows


def _merge(payload, rows, values, evidence):
    output = dict(payload)
    indicators = [dict(row) for row in payload.get('indicators', [])]
    by_start = {str(row.get('bar_start')): row for row in indicators}
    for (bar, _), value in zip(rows, values):
        key = str(bar['bar_start'])
        row = by_start.get(key)
        if row is None:
            row = dict(bar_start=bar['bar_start'], bar_end=bar['bar_end'])
            indicators.append(row)
            by_start[key] = row
        row[FIELD] = value
    output['indicators'] = sorted(indicators, key=lambda row: str(row.get('bar_start', '')))
    output['indicators_available'] = bool(indicators) or payload.get('indicators_available', False)
    output['indicator_provenance'] = dict(payload.get('indicator_provenance') or {}, session_relative_volume=evidence)
    return output


def project(payload, *, ticker, as_of, baseline, numerator):
    """Only closed-bar points; numerator authority starts at 04 ET, not viewport."""
    as_of = _clock(as_of)
    rows = _closed_rows(payload, as_of)
    start = _clock(baseline['session_start'])
    through = _clock(numerator['as_of'])
    revision = numerator.get('source_revision') or {}
    profile = numerator.get('profile')
    if (numerator.get('contract') != 'session-volume-profile-1'
            or numerator.get('ticker') != ticker
            or numerator.get('session_date') != baseline['session_date']
            or _clock(numerator['session_start']) != start
            or numerator.get('boundary_seconds') != 1
            or not start <= through <= as_of
            or revision.get('complete_for_history') is not True
            or revision.get('request_complete') is not True
            or not revision.get('token') or not revision.get('source_plan_hash')
            or not isinstance(profile, list)
            or len(profile) != floor(through.timestamp())-int(start.timestamp())+1
            or not profile or profile[0] != 0):
        raise ValueError('Chart RVOL numerator authority mismatch')
    previous = 0.
    for volume in profile:
        if type(volume) not in (int, float) or not isfinite(volume) or volume < previous:
            raise ValueError('Invalid cumulative chart volume profile')
        previous = volume
    denominator = baseline['profiles'][ticker]
    values = []
    for _, end in rows:
        # Fractional candles have no exact completed-second denominator.
        index = int(end.timestamp()-start.timestamp())
        if end.timestamp() != floor(end.timestamp()) or not 0 <= index < min(len(profile), len(denominator)):
            values.append(None)
            continue
        reference = denominator[index]
        values.append(profile[index]/reference if reference is not None and reference > 0 else None)
    return _merge(payload, rows, values, dict(status='ready', contract='session-relative-volume-1',
        baseline_hash=baseline['content_hash'], baseline_source_revision=baseline['source_revision'],
        baseline_sessions=baseline['sessions'], numerator_source_revision=revision,
        session_start=start.isoformat(), as_of=through.isoformat()))


def attach(payload, *, ticker, as_of, runtime_root, run_directory=None, pinned_hash=None):
    """Call only when the chart field was explicitly requested."""
    as_of = _clock(as_of)
    rows = _closed_rows(payload, as_of)
    if not rows:
        return payload
    day = rows[-1][1].astimezone(NY).date()
    if any(end.astimezone(NY).date() != day for _, end in rows):
        raise ValueError('Chart RVOL projection requires one session')
    root = Path(runtime_root)
    scratch = root / '_chart-rvol'
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix='selected-', dir=scratch) as directory:
            expected = {ticker: pinned_hash} if pinned_hash else None
            if pinned_hash:
                if run_directory is None:
                    raise ValueError('Pinned chart RVOL requires its run artifact')
                raw = (Path(run_directory)/'session-relative-volume'/(ticker+'.json')).read_bytes()
                (Path(directory)/(ticker+'.json')).write_bytes(raw)
            with BaselineStore(directory, day, expected,
                    shared_directory=root/'_prepared'/'session-relative-volume') as store:
                store.prepare_many([ticker])
                baseline = store.cached(ticker)
                through = max(end for _, end in rows)
                numerator = qmd_history_post_json('/features/session-volume-profile',
                    dict(session_date=str(day), ticker=ticker, as_of=through.isoformat()), timeout=180)
                return project(payload, ticker=ticker, as_of=as_of, baseline=baseline, numerator=numerator)
    except Exception as exc:
        return _merge(payload, rows, [None]*len(rows), dict(status='unavailable', reason=str(exc)))
