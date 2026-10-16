"""Minimum finite December initialization from certified price bars.

This policy initializes each frame independently from its last 50 observed
bars. It is deliberately not a claim of equivalence to infinite EMA history.
It supplies inputs to the existing indicator calculation, not a second engine.
Corporate adjustment qualification remains a separate required dependency.
"""
from datetime import date

from .contracts import digest, identifier
from .market import selected_frames
from .publication import Publisher
from .source import exchange_sessions
from pipelines.market_sip.events.market_day_sql import EMAS, FRAMES

VERSION = 'december-minimum-observed-bars-v1'


def requirements(frames=FRAMES):
    """Longest EMA period dominates MACD(26+9-1), RSI(14+1), ATR(14)."""
    frames = selected_frames(frames)
    count = max(max(EMAS), 26 + 9 - 1, 14 + 1, 14)
    return dict(version=VERSION, bars_per_frame={frame: count for frame in frames},
                initialization='first_observed_price', historical_exact=False,
                corporate_adjustment='independently_required')


def load(query, database, claim, parents, *, frames=FRAMES):
    """Read backwards, stopping when every frame has its minimum valid bars.

    Parents contain a consecutive suffix of December sessions for one listing,
    in arbitrary order. Only the sessions needed are read/receipt-verified. At most 400 price
    rows survive; sparse or newly listed securities explicitly fail rather than
    borrowing January observations or fabricating warmup bars.
    """
    identifier(database)
    frames = selected_frames(frames)
    required = requirements(frames)
    counts = required['bars_per_frame']
    parents = tuple(parents)
    expected = exchange_sessions(date(2025, 12, 1), date(2025, 12, 31))
    if not parents or len(parents) > len(expected):
        raise ValueError('Warmup requires consecutive December certified-bar locators')
    scopes = [scope for scope, _ in parents]
    if (set(scope.session_date for scope in scopes) != set(expected[-len(parents):])
            or len({(scope.dataset_id, scope.listing_key) for scope in scopes}) != 1
            or any(scope.product != 'bars' for scope in scopes)):
        raise ValueError('Warmup bar identity, product or consecutive December session coverage differs')
    publisher = Publisher(query, database, claim)
    selected = {frame: [] for frame in frames}
    used = []
    for scope, receipt in sorted(parents, key=lambda item: item[0].session_date, reverse=True):
        needed = tuple(frame for frame in frames if len(selected[frame]) < counts[frame])
        if not needed:
            break
        claim.check()
        if publisher.receipt(scope) != receipt:
            raise ValueError('Warmup requires exact certified December bar receipts')
        rows = query(f'SELECT session_date,resolution_ms,bucket_index,close_int,high_int,low_int '
                     f'FROM {database}.research_bars_v1 WHERE {scope.where(receipt["attempt_id"])} '
                     f'AND resolution_ms IN ({",".join(map(str, needed))}) '
                     'AND price_valid=1 AND extremes_valid=1 AND close_int>0 '
                     'ORDER BY resolution_ms,session_date DESC,bucket_index DESC '
                     f'LIMIT {max(counts.values())} BY resolution_ms')
        if len(rows) > len(needed) * max(counts.values()):
            raise ValueError('Warmup query exceeded its bounded row contract')
        keys = set()
        for row in rows:
            frame = int(row['resolution_ms'])
            key = (frame, int(row['bucket_index']))
            if (frame not in needed or key in keys or date.fromisoformat(str(row['session_date'])) != scope.session_date
                    or not 0 < int(row['low_int']) <= int(row['close_int']) <= int(row['high_int'])):
                raise ValueError('Warmup contains duplicate, invalid or out-of-scope price bars')
            keys.add(key)
        for frame in needed:
            candidates = sorted((row for row in rows if int(row['resolution_ms']) == frame),
                                key=lambda row: int(row['bucket_index']), reverse=True)
            selected[frame].extend(candidates[:counts[frame] - len(selected[frame])])
        if publisher.receipt(scope) != receipt:
            raise ValueError('December bar authority changed during warmup selection')
        used.append(dict(day=str(scope.session_date), parent_hash=scope.parent_hash,
                         output_hash=receipt['output_hash'], attempt_id=receipt['attempt_id']))
    missing = {frame: counts[frame] - len(rows) for frame, rows in selected.items() if len(rows) < counts[frame]}
    if missing:
        raise ValueError('Insufficient December observed bars for warmup: ' + str(missing))
    selected = {frame: tuple(sorted(rows, key=lambda row: (str(row['session_date']), int(row['bucket_index']))))
                for frame, rows in selected.items()}
    proof = dict(policy=required, dataset_id=scopes[0].dataset_id, listing_key=scopes[0].listing_key,
                 target_start='2026-01-02', parents=used, selected=selected)
    claim.check()
    return dict(proof, certificate_hash=digest(proof))
