"""Producer-owned structural population@1; no saved financial-run authority."""
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

import pyarrow as pa

from src.market_engine.completed_endpoint_return_contract import canonical_source_hash, require_hash
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.squeeze_ladder_columnar import LadderGatePolicy
from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingPolicy

VERSION = 'certified-structural-decision-population@1'
ROW_TABLE = 'arte.structural_decision_rows_v1'
WITNESS_TABLE = 'arte.structural_decision_witnesses_v1'
COUNT_TABLE = 'arte.structural_decision_counts_v1'
COVERAGE_TABLE = 'arte.structural_decision_coverage_v1'
CERTIFICATE_TABLE = 'arte.structural_decision_certification_v1'
MAX_DECISIONS = 250000
# Selected-symbol AST closure: complete reached files are sealed, but unrelated
# coordinator financial execution imports are outside the producer authority.
_CLOSURE_CACHE = None

def source_closure_inventory():
    global _CLOSURE_CACHE
    root = Path(__file__).resolve().parents[2]
    if _CLOSURE_CACHE is not None:
        inventory, stamps = _CLOSURE_CACHE
        current = tuple(((root/name).stat().st_mtime_ns, (root/name).stat().st_size) for name, symbols in inventory)
        if current == stamps: return inventory
    inventory = _discover_source_closure()
    stamps = tuple(((root/name).stat().st_mtime_ns, (root/name).stat().st_size) for name, symbols in inventory)
    _CLOSURE_CACHE = (inventory, stamps)
    return inventory


def _discover_source_closure():
    import ast
    root = Path(__file__).resolve().parents[2]
    own = (
        'src.market_engine.structural_decision_population_contract',
        'src.market_engine.structural_decision_insert_authority',
        'pipelines.market_sip.events.structural_decision_population_producer',
        'src.backend.backtest_structural_decision_population_store',
    )
    pending = [(module, '*') for module in own]
    pending += [('src.backend.backtest_ladder_coordinator', name) for name in
        ('prepare_ladder_campaign', 'completed_cross_indices',
         'PreparedLadderProposal', 'PreparedLadderCampaign')]
    selected, parsed = {}, {}
    while pending:
        module, symbol = pending.pop()
        path = root.joinpath(*module.split('.')).with_suffix('.py')
        if not path.is_file():
            continue  # External library; only actual repository files are sealed.
        if symbol in selected.setdefault(module, set()):
            continue
        selected[module].add(symbol)
        if module not in parsed: parsed[module] = ast.parse(path.read_text(encoding='utf-8-sig'))
        tree = parsed[module]
        bindings = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for child in ast.walk(target):
                        if isinstance(child, ast.Name): bindings[child.id] = node
        if symbol == '*':
            nodes = list(tree.body)
        elif symbol in bindings:
            nodes = [bindings[symbol]]
        else:
            nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                     and any((alias.asname or alias.name.split('.')[0]) == symbol for alias in node.names)]
            if not nodes: raise ValueError(f'Unresolved local source symbol: {module}.{symbol}')
        names = {n.id for node in nodes for n in ast.walk(node) if isinstance(n, ast.Name)}
        names.add(symbol)
        for name in names.intersection(bindings):
            pending.append((module, name))
        imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        imports += [n for node in nodes for n in ast.walk(node)
                    if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in nodes:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == '__import__':
                    raise ValueError(f'Unresolved dynamic import in source closure: {module}')
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'import_module':
                    # Reviewed external truststore SSLContext unwrap; no repository algorithm dispatch.
                    if module == 'src.trading_runtime.keeper_session' and ast.unparse(call) == 'importlib.import_module(context_type.__module__)':
                        continue
                    raise ValueError(f'Unresolved dynamic import in source closure: {module}')
        for item in imports:
            if isinstance(item, ast.ImportFrom):
                prefix = module.split('.')[:-item.level] if item.level else []
                base = '.'.join(prefix + ([item.module] if item.module else []))
                for alias in item.names:
                    bound = alias.asname or alias.name
                    if symbol != '*' and bound not in names: continue
                    if alias.name == '*': raise ValueError('Wildcard local imports require explicit source anchors')
                    candidate = root.joinpath(*base.split('.')).with_suffix('.py')
                    sub = root.joinpath(*(base+'.'+alias.name).split('.')).with_suffix('.py')
                    if candidate.is_file(): pending.append((base, alias.name))
                    elif sub.is_file(): pending.append((base+'.'+alias.name, '*'))
            else:
                for alias in item.names:
                    bound = alias.asname or alias.name.split('.')[0]
                    if symbol != '*' and bound not in names: continue
                    candidate = root.joinpath(*alias.name.split('.')).with_suffix('.py')
                    if candidate.is_file():
                        attrs = {n.attr for node in nodes for n in ast.walk(node)
                                 if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                                 and n.value.id == bound}
                        if not attrs: raise ValueError(f'Unresolved local module dispatch: {alias.name}')
                        pending.extend((alias.name, attr) for attr in attrs)
    return tuple((module.replace('.', '/')+'.py', tuple(sorted(symbols)))
                 for module, symbols in sorted(selected.items()))





class StructuralWindow(str, Enum):
    PREMARKET = 'premarket'
    AFTERHOURS = 'afterhours'


@dataclass(frozen=True, slots=True)
class StructuralDecisionDeclaration:
    """Actual source-only parameters; no number, account, run or capital fields."""
    window: StructuralWindow
    gate: LadderGatePolicy
    geometry: LadderGeometryBindingPolicy | None = None
    tick_int: int = 100
    stop_buffer_ticks: int = 1
    break_buffer_ticks: int = 1

    def __post_init__(self):
        if type(self.window) is not StructuralWindow or type(self.gate) is not LadderGatePolicy:
            raise ValueError('Structural declaration requires typed full window and native gate')
        self.gate.__post_init__()
        if self.gate.acquisition_windows != ((0,19800000),(43200000,57600000)):
            raise ValueError('Structural baseline requires full PM/AH acquisition windows')
        if self.geometry is not None:
            if type(self.geometry) is not LadderGeometryBindingPolicy:
                raise ValueError('Structural geometry declaration is foreign')
            self.geometry.__post_init__()
        if (self.tick_int,self.stop_buffer_ticks,self.break_buffer_ticks) != (100,1,1) or any(
                type(v) is not int for v in (self.tick_int,self.stop_buffer_ticks,self.break_buffer_ticks)):
            raise ValueError('Structural baseline tick/buffer policy changed')

    @property
    def start_boundary_ms(self):
        return 0 if self.window is StructuralWindow.PREMARKET else 43200000

    @property
    def end_boundary_ms(self):
        return 19800000 if self.window is StructuralWindow.PREMARKET else 57600000

    @property
    def digest(self):
        return sha256(canonical_json(dict(version=VERSION, window=self.window.value,
            gate=asdict(self.gate), geometry=self.geometry.payload() if self.geometry else None,
            tick_int=self.tick_int,stop_buffer_ticks=self.stop_buffer_ticks,
            break_buffer_ticks=self.break_buffer_ticks, source_start_ms=0,
            source_end_ms=self.end_boundary_ms, admission='start<available<=end; no pre16 admission carry',
            targets=3,allocation='equal',same_clock='earliest qualified frozen setup')).encode()).hexdigest()


def implementation_hash():
    root=Path(__file__).resolve().parents[2]
    return canonical_source_hash([(name,(root/name).read_bytes()) for name, symbols in source_closure_inventory()])


@dataclass(frozen=True, slots=True)
class StructuralSourcePlan:
    market: object
    declaration: StructuralDecisionDeclaration
    prices: object
    identities: object
    seeds: object
    pivots: object
    intervals: object
    scan: object
    token: str

    def __post_init__(self):
        from src.backend.backtest_market_data import CertifiedMarketDayPlan
        from src.backend.backtest_liquidity_price import PriceLevelPlan
        from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
        from src.backend.structural_v7_seed import CertifiedSeedPlan
        from src.backend.backtest_declared_ladder_plan import CertifiedGeometryScopes
        if (type(self.market) is not CertifiedMarketDayPlan or len(self.market.sessions)!=1
                or type(self.declaration) is not StructuralDecisionDeclaration
                or type(self.prices) is not PriceLevelPlan or self.prices.projected(self.market)!=self.prices
                or type(self.identities) is not CertifiedIdentityPlan
                or self.identities.source_build_id!=self.market.build_id
                or self.identities.session_date!=self.market.sessions[0]
                or self.identities.market_token!=self.market.token or self.identities.tickers!=self.market.tickers
                or type(self.seeds) is not CertifiedSeedPlan or self.seeds.build_id!=self.market.build_id
                or self.seeds.provisional is not False):
            raise ValueError('Structural source plan lacks actual complete parent contracts')
        self.declaration.__post_init__()
        for geometry in (self.pivots,self.intervals):
            if (type(geometry) is not CertifiedGeometryScopes or geometry.ticker_count!=len(self.market.tickers)
                    or tuple(t for scope in geometry.scopes for t in scope[0])!=self.market.tickers
                    or sum(scope[2] for scope in geometry.scopes)!=len(self.market.tickers)):
                raise ValueError('Structural geometry certificates omit frozen full universe')
            require_hash(geometry.token)
        for value in (self.market.token,self.prices.token,self.identities.token,self.seeds.token,self.token): require_hash(value)
        from src.backend.backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
        verify_declared_ladder_seed_plan(self.market,self.seeds)
        if source_token(self.market,self.declaration,self.prices,self.identities,self.seeds,self.pivots,self.intervals,self.scan)!=self.token:
            raise ValueError('Structural source plan seal changed')

    @property
    def attempt(self):
        return str(uuid5(UUID('8db92de6-35f4-47dd-8e4b-f7bb4b254047'),self.token))


@dataclass(frozen=True,eq=False)
class StructuralPopulationPacket:
    source: StructuralSourcePlan
    rows: pa.Table
    witnesses: pa.Table
    counts: pa.Table
    coverage: pa.Table
    certificate: pa.Table


def partition_ticker_tables(table):
    """One columnar partition pass; hashing never rescans the entire universe."""
    import polars as pl
    return {key[0]:frame.to_arrow().cast(table.schema) for key,frame in
            pl.from_arrow(table).partition_by('ticker',as_dict=True,maintain_order=True).items()}


def source_token(market,declaration,prices,identities,seeds,pivots,intervals,scan):
    return sha256(canonical_json(dict(version=VERSION,implementation_hash=implementation_hash(),
        market=market.token,declaration=declaration.digest,prices=prices.token,
        identities=identities.token,seeds=seeds.token,pivots=pivots.token,
        intervals=intervals.token,scan=scan['authority'])).encode()).hexdigest()


def certify_structural_sources(market,declaration,client):
    """Certify the WHOLE frozen universe before trimming admitted contexts.

    Reuses actual canonical products and exact prior NYSE seed policy. No saved
    run/configuration impersonation and no missing-product derivation fallback.
    """
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, verify_market_day_plan
    from src.backend.backtest_liquidity_price import certify_price_level_plan
    from src.backend.backtest_strategy_one_identity import certify_identity_plan
    from src.backend.structural_v7_seed import certified_seed_plan
    from src.backend.backtest_declared_ladder_seed import verify_declared_ladder_seed_plan
    from src.backend.backtest_declared_ladder_plan import _geometry_scopes
    from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
    from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
    from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
    if (type(market) is not CertifiedMarketDayPlan or len(market.sessions)!=1 or not market.tickers
            or market.tickers!=tuple(sorted(set(market.tickers))) or len(market.tickers)>8192):
        raise ValueError('Structural producer requires full certified one-day universe')
    if type(declaration) is not StructuralDecisionDeclaration:
        raise ValueError('Structural source declaration is untyped')
    declaration.__post_init__()
    date.fromisoformat(market.sessions[0])
    if market.execution_interval.kind!='fixed' or market.execution_interval.milliseconds!=100:
        raise ValueError('Structural producer requires native100ms authority')
    if client.execute("SELECT getSetting('readonly')").strip()!='1':
        raise ValueError('Structural source certification requires SELECT-only principal')
    verify_market_day_plan(market,client)
    prices=certify_price_level_plan(market,client)
    if prices.projected(market)!=prices:
        raise ValueError('Structural price authority omits full universe')
    identities=certify_identity_plan(market,client=client)
    seeds=certified_seed_plan(market,client)
    verify_declared_ladder_seed_plan(market,seeds)
    pivots,intervals=_geometry_scopes(market,seeds,client,certify_pivot_plan,certify_v7_interval_plan)
    stream,activation=canonical_stream_activation()
    scan=load_first_squeeze_occurrences(market,stream=stream,activation=activation,
        through_boundary_ms=declaration.end_boundary_ms,client=client)
    return StructuralSourcePlan(market,declaration,prices,identities,seeds,pivots,intervals,scan,
        source_token(market,declaration,prices,identities,seeds,pivots,intervals,scan))


COMMON=[('build_id',pa.string()),('session_date',pa.date32()),('attempt_id',pa.string())]
KEY=[('ticker',pa.string()),('boundary_ms',pa.uint32())]
ROW_SCHEMA=pa.schema(COMMON+KEY+[(name,pa.uint32()) for name in ('admission_boundary_ms','qualification_boundary_ms')]
    +[(name,pa.uint64()) for name in ('previous_close_int','close_int','entry_limit_int','resistance_int','stop_int')]
    +[(name,pa.string()) for name in ('resistance_id','stop_pivot_id','target1_id','target2_id','target3_id')]
    +[('eligible_notional',pa.float64())])
WITNESS_SCHEMA=pa.schema(COMMON+KEY+[(name,pa.string()) for name in
    ('market_token','scan_hash','v7_token','pivot_token','source_plan_token','declaration_digest')]
    +[('source_row_index',pa.uint32()),('resistance_confirmed_epoch_ms',pa.uint64()),
      ('qualification_vwap_bits',pa.uint64()),('stop_pivot_boundary_ms',pa.uint32()),
      ('stop_confirmed_boundary_ms',pa.uint32()),('resistance_lower',pa.float64()),('resistance_upper',pa.float64())])
COUNT_SCHEMA=pa.schema(COMMON+[('reason',pa.string()),('count',pa.uint64())])
COVERAGE_SCHEMA=pa.schema(COMMON+[('ticker',pa.string()),('decision_count',pa.uint32()),
    ('rows_hash',pa.string()),('witnesses_hash',pa.string()),('market_token',pa.string()),('source_plan_token',pa.string())])
CERTIFICATE_SCHEMA=pa.schema(COMMON+[(name,pa.string()) for name in
    ('source_plan_token','declaration_digest','implementation_hash','population_hash','rows_hash','witnesses_hash','counts_hash','coverage_hash')]
    +[('ticker_count',pa.uint32()),('decision_count',pa.uint32()),('start_boundary_ms',pa.uint32()),('end_boundary_ms',pa.uint32())])
TABLE_SCHEMAS={ROW_TABLE:ROW_SCHEMA,WITNESS_TABLE:WITNESS_SCHEMA,COUNT_TABLE:COUNT_SCHEMA,
    COVERAGE_TABLE:COVERAGE_SCHEMA,CERTIFICATE_TABLE:CERTIFICATE_SCHEMA}
