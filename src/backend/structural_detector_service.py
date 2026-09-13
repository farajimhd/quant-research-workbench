"""Read-only indicator projection over the chart's realized candle contract.

Chart candles are inputs, never persisted as market authority. Global evidence
defaults to a matching certified V6 cursor, with price-basis checks. Strategies may
consume the same market_engine detector directly without any browser or API.
"""
from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock
from time import monotonic

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.market_engine.structural_detector import DetectorSettings, StructuralDetector, VERSION

router = APIRouter(prefix='/api/indicators/structural-detector', tags=['indicators'])
_lock = RLock()
_cache = OrderedDict()


class Candle(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    time: float
    end: float
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    reversal_bps: float = Field(default=50, ge=1, le=1000)
    volatility_multiple: float = Field(default=2, ge=.1, le=10)
    body_half_life: float = Field(default=5, ge=1, le=100)
    consolidation_body_multiple: float = Field(default=.25, ge=.01, le=2)
    proximity_body_multiple: float = Field(default=1, ge=.1, le=10)
    macd_gap_bps: float = Field(default=25, ge=.1, le=1000)
    macd_change_bps: float = Field(default=.1, gt=0, le=100)
    momentum_confirm_closes: int = Field(default=2, ge=1, le=20)
    rsi_period: int = Field(default=14, ge=2, le=200)
    rsi_neutral_band: float = Field(default=5, gt=0, lt=20)
    rsi_change_points: float = Field(default=.5, gt=0, le=100)
    signal_setup_candles: int = Field(default=60, ge=1, le=10000)
    signal_confirmation_candles: int = Field(default=3, ge=1, le=10000)
    signal_hold_candles: int = Field(default=300, ge=1, le=10000)
    signal_min_reward_risk: float = Field(default=1.5, gt=0, le=100)
    signal_stop_atr: float = Field(default=.1, gt=0, le=100)
    signal_zone_atr: float = Field(default=.2, gt=0, le=10)
    signal_min_stop_atr: float = Field(default=1, gt=0, le=100)
    signal_max_risk_atr: float = Field(default=4, gt=0, le=100)
    signal_max_extension_atr: float = Field(default=3, gt=0, le=100)
    signal_min_room_atr: float = Field(default=1, gt=0, le=100)
    signal_progress_candles: int = Field(default=20, ge=1, le=10000)
    signal_follow_through_candles: int = Field(default=5, ge=1, le=10000)
    signal_profit_activation_r: float = Field(default=1, gt=0, le=100)
    signal_profit_giveback_fraction: float = Field(default=.4, gt=0, lt=1)
    tail_range_fraction: float = Field(default=.5, ge=.1, le=1)
    indecision_body_fraction: float = Field(default=.2, ge=.01, le=1)
    expansion_body_multiple: float = Field(default=1.5, ge=.1, le=10)
    expansion_body_fraction: float = Field(default=.65, ge=.1, le=1)
    movement_body_multiple: float = Field(default=.1, ge=.01, le=2)
    movement_min_bps: float = Field(default=1, ge=.01, le=100)
    deep_correction_multiple: float = Field(default=2, ge=.5, le=20)
    evidence_memory_candles: int = Field(default=1800, ge=10, le=20000)
    pressure_closes: int = Field(default=2, ge=2, le=20)
    volume_half_life: float = Field(default=5, ge=1, le=100)
    volume_change_fraction: float = Field(default=.1, ge=.01, le=1)
    volume_warmup_candles: int = Field(default=5, ge=1, le=100)
    session_level_count: int = Field(default=3, ge=1, le=10)
    volume_expansion_multiple: float = Field(default=1.5, ge=1, le=20)
    volume_divergence_min_score: float = Field(default=30, ge=1, le=100)
    volume_setup_max_candles: int = Field(default=20, ge=1, le=1000)
    atr_period: int = Field(default=14, ge=2, le=200)
    atr_warmup_candles: int = Field(default=5, ge=1, le=200)
    break_body_atr: float = Field(default=.3, gt=0, le=10)
    break_body_fraction: float = Field(default=.4, gt=0, le=1)
    penetration_atr: float = Field(default=.1, gt=0, le=5)
    acceptance_closes: int = Field(default=2, ge=2, le=20)


class DetectorRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    ticker: str = Field(pattern=r'^[A-Za-z0-9.\-]{1,20}$')
    timeframe: str = Field(min_length=1, max_length=8)
    as_of: float
    candles: list[Candle] = Field(max_length=50000)
    settings: Settings = Field(default_factory=Settings)
    book_id: str | None = None
    split_adjusted: bool = False


class GlobalContext:
    def __init__(self, ticker, book_id):
        from src.backend.experimental_structure_book import builds, resolve
        self.ticker = ticker
        self.books = [resolve(book_id)] if book_id else sorted(
            [b for b in builds() if b['ticker']==ticker and b['version']=='causal-level-book-v7-mle-1'],
            key=lambda b:(b['end'], b.get('selection_contract')=='symmetric-level-evidence-selection-2', b['id']), reverse=True)
        if any(b['ticker'] not in ('*',ticker) or b['version']!='causal-level-book-v7-mle-1' for b in self.books):
            raise ValueError('Global context requires a matching V7 book')
        self.cursor = None
        self.book = None
        self.errors = {}

    def at(self, bar):
        from src.backend.v7_book_cursor import V7BookCursor as SwingBookCursor
        from src.backend.swing_book_source import NY
        at = datetime.fromtimestamp(bar['end'], timezone.utc)
        session = at.astimezone(NY).date().isoformat()
        book = next((b for b in self.books if b['start'] <= session <= b['end']), None)
        if not book:
            return None, 'no_verified_v7_book_for_session'
        if session in self.errors:
            return None, self.errors[session]
        try:
            if not self.book or self.book['id']!=book['id']:
                self.cursor = SwingBookCursor(book['id'], self.ticker, book['fingerprint'])
                self.book = book
            snap = self.cursor.snapshot(at)
            # The book and chart can apply different trade-eligibility rules;
            # equal closes are not a test of split basis. Adjusted charts are
            # explicitly excluded by the request's price-basis contract below.
            return snap['unified_levels'], 'available'
        except (ValueError, RuntimeError, OSError) as exc:
            self.errors[session] = 'global_source_unavailable: '+str(exc)
            return None, self.errors[session]


def calculate(request, context_factory=GlobalContext):
    candles = [c.model_dump() for c in request.candles]
    # Validate the whole input before returning or advancing cached state.
    for index, bar in enumerate(candles):
        if bar['end'] <= bar['time'] or not bar['low'] <= min(bar['open'],bar['close']) <= max(bar['open'],bar['close']) <= bar['high']:
            raise ValueError('Invalid candle geometry')
        if index and bar['time'] < candles[index-1]['end']:
            raise ValueError('Candle input is not ordered and non-overlapping')
    closed = [bar for bar in candles if bar['end'] <= request.as_of]
    settings = request.settings.model_dump()
    key = (request.ticker.upper(), request.timeframe, closed[0]['time'] if closed else None,
           tuple(settings.items()), request.book_id, request.split_adjusted)
    with _lock:
        now = monotonic()
        for old in [k for k,v in _cache.items() if now-v['used']>300]:
            del _cache[old]
        entry = _cache.get(key)
        common = min(len(closed), len(entry['bars'])) if entry else 0
        if entry is None or closed[:common] != entry['bars'][:common]:
            entry = dict(engine=StructuralDetector(DetectorSettings(**settings)),
                context=context_factory(request.ticker.upper(), request.book_id), bars=[], rows=[], used=now)
            _cache[key] = entry
        for bar in closed[len(entry['bars']):]:
            levels, status = (None, 'split_adjusted_chart_requires_matching_global_basis') if request.split_adjusted else entry['context'].at(bar)
            previous=entry['engine'].last
            cursor=getattr(entry['context'],'cursor',None)
            proof=cursor.empty_interval(previous['end'],bar['time']) if previous and status=='available' and cursor and hasattr(cursor,'empty_interval') else None
            result = entry['engine'].observe(bar, levels, status, continuity=proof)
            entry['rows'].append(result)
            entry['bars'].append(bar)
        entry['used'] = now
        _cache.move_to_end(key)
        while len(_cache)>8:
            _cache.popitem(last=False)
        rows = entry['rows'][:len(closed)]
        book = entry['context'].book
        return dict(contract=VERSION, rows=rows, pending_count=len(candles)-len(closed),
            context_start=closed[0]['time'] if closed else None,
            global_book={k:book[k] for k in ('id','fingerprint','version')} if book else None,
            settings=settings, candle_authority='realized chart candles; no forecasts',
            global_available_count=sum(r['global_status']=='available' for r in rows))


@router.post('')
def detect(request: DetectorRequest):
    try:
        return calculate(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, 'Structural detector source failed: '+str(exc)) from exc
