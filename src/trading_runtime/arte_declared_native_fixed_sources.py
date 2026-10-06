"""Independent prepared declared-family readback, deliberately not installed.

Journal identity selects a source to reconstruct, never its policy, population,
admission mask or financial state. Existing installed-family prefix recovery
cannot attest a future own-family predecessor. Historical/native acceptance and
manager restoration therefore remain closed until an installed source hook is
reviewed. Producer reconstruction below is callable without that approval.
"""
from dataclasses import dataclass, asdict, fields
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from uuid import UUID, uuid5, NAMESPACE_URL

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, market_day_boundary,
    verify_market_day_plan, _literal, assert_select_only,
)
from src.backend.backtest_declared_native_fixed_plan import (
    compile_declared_momentum_plan, load_declared_entry_source_plan, _attempts, _arrow,
)
from src.backend.backtest_declared_native_fixed_entry import (
    DeclaredNativeFixedEntryProposal, ENTRY_FAMILY,
)
from .declared_native_fixed_candidate import NativeFixedCandidateSpec, verify_prepared_candidate_configuration
from .strategy_initial_price_break import FirstSetupPriceBreakWitness, first_setup_price_break
from .strategy_entry_activity_fade import EntryActivityCandle
from .strategy_entry_activity_witness import EntryActivityWitness, validate_entry_activity_witness
from .entry_spread_risk import exact_epoch_us
from .journal_contract import canonical_json
from .declared_native_entry_source import DeclaredNativeEntrySourcePolicy
from .arte_journal_commit_v4 import V4CommittedPrefix

QUOTE_CONTRACT = "declared-entry-spread-risk-quote-source@2"


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
        raise ValueError("Declared source requires canonical nonzero run identity")
    return value


@dataclass(frozen=True, slots=True)
class ReconstructedDeclaredEntrySource:
    proposal: DeclaredNativeFixedEntryProposal
    momentum: object
    initial: object
    first_price: FirstSetupPriceBreakWitness
    activity: EntryActivityWitness
    quote: tuple[int, int, int, int]
    configuration_hash: str
    content_hash: str

    def payload(self):
        return {f.name: getattr(self, f.name) if f.name in ("configuration_hash",)
                else asdict(getattr(self, f.name)) if f.name not in ("quote", "content_hash")
                else list(self.quote) for f in fields(self) if f.name != "content_hash"}


@dataclass(frozen=True, slots=True)
class DeclaredCompletedProducerFacts:
    run_id: str
    configuration_hash: str
    market_plan_token: str
    source_build_id: str
    source_attempts: tuple[str, str, str]
    session_date: str
    ticker: str
    boundary_ms: int
    resolution_ms: int
    completed_boundary_ms: int
    close_int: int
    price_valid: bool
    macd_line: float | None
    macd_signal: float | None
    bid_int: int
    ask_int: int
    quote_timestamp_us: int
    quote_valid: bool
    content_hash: str

    def payload(self):
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "content_hash"}


@dataclass(frozen=True, slots=True)
class DeclaredHistoricalPredecessor:
    """Requested historical identity only, never a self-attesting snapshot."""
    run_id: str
    batch_id: str
    prior_batch_id: str
    prior_sequence: int
    boundary_ms: int
    configuration_hash: str
    market_plan_token: str
    prefix: V4CommittedPrefix
    portfolio_state_hash: str
    broker_snapshot_hash: str

    def __post_init__(self):
        for value in (self.run_id, self.batch_id, self.prior_batch_id):
            _uuid(value)
        if (type(self.prior_sequence) is not int or self.prior_sequence < 1
                or type(self.boundary_ms) is not int or not 0 < self.boundary_ms <= 57_600_000
                or self.boundary_ms % 100
                or any(type(v) is not str or len(v) != 64 or v=='0'*64 or any(c not in '0123456789abcdef' for c in v)
                       for v in (self.configuration_hash, self.market_plan_token,
                                 self.portfolio_state_hash, self.broker_snapshot_hash))
                or type(self.prefix) is not V4CommittedPrefix
                or self.prefix.run_id != self.run_id or self.prefix.last_sequence != self.prior_sequence
                or self.prefix.last_batch_id != self.prior_batch_id or self.prefix.status != 'running'
                or not self.prefix.batch_ids or self.prefix.batch_ids[-1] != self.prior_batch_id):
            raise ValueError("Declared historical predecessor has invalid scope")


class PreparedDeclaredSourceResolver:
    """Fresh independent component reader; no financial/native acceptance.

    The market argument is a pinned plan descriptor, independently verified on
    every operation. Complete prepared config is independently recompiled from
    normalized parent/source authority. No cached caller plan is authoritative.
    """
    def __init__(self, client, *, run_id, candidate, envelope, approval, market, source_policy):
        _uuid(run_id)
        if (type(candidate) is not NativeFixedCandidateSpec or type(market) is not CertifiedMarketDayPlan
                or type(source_policy) is not DeclaredNativeEntrySourcePolicy):
            raise ValueError("Declared source needs exact candidate and market types")
        source_policy.__post_init__()
        self.client, self.run_id, self.candidate, self.market = client, run_id, candidate, market
        self.source_policy = source_policy
        # Detached canonical immutable configuration inputs, rechecked on read.
        self._envelope = canonical_json(envelope)
        self._approval = canonical_json(approval)
        self._binding()

    def _verify_configuration(self, envelope, approval):
        verify_prepared_candidate_configuration(self.client, self.candidate, envelope, approval=approval)

    def _binding(self):
        from .arte_journal_writer import load_typed_run_context
        from .arte_backtest_definition import load_backtest_definition
        if self.client.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError("Declared source requires a SELECT-only reader")
        if type(self.source_policy) is not DeclaredNativeEntrySourcePolicy:
            raise ValueError("Declared source needs exact explicit source policy")
        self.source_policy.__post_init__()
        envelope, approval = json.loads(self._envelope), json.loads(self._approval)
        self._verify_configuration(envelope, approval)
        native = load_typed_run_context(self.client, self.run_id)
        identity = self.candidate.base.identity
        if (native['run_id'] != self.run_id or native['mode'] != 'backtest'
                or native['strategy_id'] != identity.strategy_id
                or type(native['strategy_revision']) is not int or native['strategy_revision'] != identity.revision
                or native['configuration_hash'] != envelope['payload_hash']
                or native['market_plan_token'] != self.market.token
                or native['session_date'] not in self.market.sessions
                or self.market.sessions != (native['session_date'],)
                or type(native['evaluation_interval_ms']) is not int
                or native['evaluation_interval_ms'] != self.market.execution_interval.milliseconds):
            raise ValueError("Declared source differs from fenced own run/configuration")
        saved = load_backtest_definition(self.client, self.run_id, run_context=native)
        definition = saved['definition']
        mode = definition['ticker_population_mode']
        tickers = tuple(row['ticker'] for row in saved['tickers'])
        if not ((mode == 'market_plan' and not tickers)
                or (mode == 'explicit' and tickers == tuple(self.market.tickers))):
            raise ValueError("Declared source population differs from fenced membership")
        if (not self.market.tickers or len(self.market.tickers) > 8192
                or tuple(sorted(set(self.market.tickers))) != tuple(self.market.tickers)
                or definition['final_session_date'] != native['session_date']
                or type(definition['end_local_ms']) is not int):
            raise ValueError("Declared source requires exact bounded single-session population")
        end = definition['end_local_ms'] - SESSION_OPEN_OFFSET_MS
        if not 0 < end <= 57_600_000 or end % 100:
            raise ValueError("Declared source has invalid fenced source extent")
        verify_market_day_plan(self.market, self.client)
        return native, saved, envelope, end

    def reload_entry_plan(self):
        """Reconstruct complete saved horizon; never narrow from proposal/token."""
        from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan, RULE_DIGEST
        from src.backend.backtest_strategy_one_activation import load_strategy_one_activations
        from src.backend.structural_v7_seed import certified_seed_plan
        from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
        from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
        from src.backend.backtest_strategy_one_entry_store import certify_entry_evidence_plan
        from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
        from src.backend.backtest_declared_base_entry_gate import compile_declared_base_entry_gate
        native, saved, envelope, end = self._binding()
        candidates = certify_candidate_plan(self.market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=end, client=self.client)
        if candidates.excluded_tickers:
            raise ValueError("Declared source exclusion product needs independently fenced generic cohort metadata; unsupported")
        activations = load_strategy_one_activations(self.market, candidates, client=self.client)
        seeds = certified_seed_plan(self.market, self.client)
        tickers = tuple(row.ticker for row in candidates.prepared)
        pivots = certify_pivot_plan(self.market, session_date=native['session_date'], candidate_tickers=tickers, client=self.client)
        hod = certify_hod_plan(self.market, candidates, seeds, client=self.client)
        entry = certify_entry_evidence_plan(self.market, candidates, activations, pivots, hod, seeds, client=self.client)
        base = compile_declared_base_entry_gate(candidates, entry, capabilities=self.candidate.base)
        momentum = load_rising_momentum_plan(self.market, candidates, client=self.client, candidate_indices=base.eligible_indices)
        parent = compile_declared_momentum_plan(self.candidate.base, self.market, candidates, entry, momentum)
        source = load_declared_entry_source_plan(parent, client=self.client, candidate=self.candidate,
                                                quote_source_contract=self.source_policy.quote_source_contract)
        return source, native, saved, envelope

    def reconstruct_entry(self, proposal):
        """Authoritative assignment admission remains closed for this component.

        The saved definition contains assignment IDs only. Neither independent
        account/assignment membership nor a journal token proves their pairing
        with ticker and own strategy. Packet3 must reload the independently
        attested assignment base and historical command state before admission.
        """
        self.reconstruct_entry_facts(proposal)
        raise ValueError("Declared own-family installed assignment scope hook is missing; entry admission remains closed")

    def reconstruct_entry_facts(self, proposal):
        """Producer/source equivalence only, without assignment scope approval."""
        return self._entry_facts(proposal, self.reload_entry_plan())

    def _entry_facts(self, proposal, loaded):
        if type(proposal) is not DeclaredNativeFixedEntryProposal:
            raise ValueError("Declared source needs exact own entry family")
        proposal.__post_init__()
        source, native, saved, envelope = loaded
        parent, identity = source.parent, self.candidate.base.identity
        if (proposal.run_id != self.run_id or proposal.account_id not in native['account_ids']
                or proposal.assignment_id not in tuple(row['assignment_id'] for row in saved['assignments'])
                or proposal.strategy_number != identity.strategy_number
                or proposal.strategy_id != identity.strategy_id or proposal.revision != identity.revision):
            raise ValueError("Declared entry has foreign run/own revision/account/assignment")
        index = parent.index(proposal.ticker, proposal.boundary_ms)
        if not source.eligible_mask[index]:
            raise ValueError("Declared entry is not in reconstructed causal selection")
        fact = parent.base.facts[index]
        activation, = (a for a in parent.entry.activations
            if (a.ticker, a.episode_start_ms) == (fact.ticker, fact.episode_start_ms))
        quote = tuple(int(a[index]) for a in source.quote_columns)
        intent = str(uuid5(NAMESPACE_URL, json.dumps((ENTRY_FAMILY, self.run_id,
            str(identity.strategy_number), identity.strategy_id, str(identity.revision), source.token,
            proposal.assignment_id, proposal.account_id, fact.ticker, str(fact.boundary_ms),
            str(fact.episode_start_ms)), separators=(',', ':'))))
        expected = DeclaredNativeFixedEntryProposal(self.run_id, identity.strategy_number, identity.strategy_id,
            identity.revision, source.token, intent, proposal.assignment_id, proposal.account_id, fact.ticker,
            fact.boundary_ms, fact.episode_start_ms, quote[1]/10_000, fact.stop_price, fact.target_price,
            fact.target_level_id, activation.average_gap, fact.bos_break_boundary_ms, fact.bos_support_level_id)
        if proposal != expected:
            raise ValueError("Declared entry differs from independently reconstructed complete source")
        first = int(parent.first_indices[index])
        price = FirstSetupPriceBreakWitness(fact.ticker, parent.momentum.keys[first][1],
            self.market.build_id, source.source_attempts[0][first], self.market.token,
            *(int(a[first]) for a in source.price_columns[:4]), *(bool(a[first]) for a in source.price_columns[4:]))
        if not first_setup_price_break(price):
            raise ValueError("Declared first price witness does not qualify")
        history = self.candidate.base.payload()['inherited']['policies']['entry_activity_policy']['history_activation_ms']
        from .strategy_entry_activity_fade import AFTERHOURS_START_MS
        opening = AFTERHOURS_START_MS if fact.boundary_ms >= AFTERHOURS_START_MS else 0
        candles = tuple(EntryActivityCandle(int(source.activity_columns[0][index,j]), int(source.activity_columns[1][index,j]))
            if fact.boundary_ms >= opening + history and source.activity_columns[2][index,j] else None for j in range(4))
        activity = validate_entry_activity_witness(EntryActivityWitness(fact.ticker, native['session_date'],
            fact.boundary_ms, self.market.build_id, source.source_attempts[0][index], self.market.token,
            source.token, parent.candidates.token, parent.entry.token, parent.token, candles))
        current, initial = parent.momentum.lookup(fact.ticker, fact.boundary_ms), parent.initial(index)
        value = ReconstructedDeclaredEntrySource(expected, current, initial, price, activity, quote, envelope['payload_hash'], '')
        return ReconstructedDeclaredEntrySource(expected, current, initial, price, activity, quote,
                                               envelope['payload_hash'], _digest(value.payload()))

    def compare_entry_witnesses(self, proposal, witnesses):
        expected = self.reconstruct_entry_facts(proposal)
        if type(witnesses) is not ReconstructedDeclaredEntrySource or canonical_json(witnesses.payload()) != canonical_json(expected.payload()) or witnesses.content_hash != expected.content_hash:
            raise ValueError("Declared source witness fields or content hash differ")
        return expected

    def completed_producer_facts(self, ticker, boundary_ms):
        """Read existing completed5s price/MACD/current quote, not held state.

        This is the producer portion of inherited management witnesses. First
        held clock, original order/fills, quantity, pending exit, prior arm and
        protection transition still require exact historical native authority.
        Missing source rows remain missing; this method never carries or builds.
        """
        import pyarrow as pa
        native, _, envelope, end = self._binding()
        if (type(ticker) is not str or ticker not in self.market.tickers
                or type(boundary_ms) is not int or not 0 < boundary_ms <= end or boundary_ms % 100):
            raise ValueError("Declared management source is outside fenced scope")
        # Exact recognized inherited producer resolution, not a caller flag.
        resolution = self.candidate.base.payload()['inherited']['policies']['entry_activity_policy']['resolution_ms']
        clock = boundary_ms//resolution*resolution
        bucket = (clock+SESSION_OPEN_OFFSET_MS)//resolution-1
        from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan, RULE_DIGEST
        candidates=certify_candidate_plan(self.market,candidate_rule_digest=RULE_DIGEST,through_boundary_ms=end,client=self.client)
        attempts=tuple(_attempts(self.market,candidates,((ticker,boundary_ms),),stage)[0]
                       for stage in ('bars','technical','broker_100ms'))
        common = f"build_id={_literal(self.market.build_id)} AND session_date=toDate({_literal(native['session_date'])}) AND ticker={_literal(ticker)}"
        sql = f"SELECT bucket_index,resolution_ms,close_int,price_valid FROM arte.bars_v1 WHERE {common} AND attempt_id=toUUID({_literal(attempts[0])}) AND resolution_ms={resolution} AND bucket_index={bucket} FORMAT ArrowStream"
        bars = _arrow(self.client,sql,('bucket_index','resolution_ms','close_int','price_valid'),
                      (pa.uint32(),pa.uint32(),pa.uint64(),pa.uint8()),{bucket})
        sql = f"SELECT bucket_index,resolution_ms,macd_line,macd_signal FROM arte.indicators_v1 WHERE {common} AND attempt_id=toUUID({_literal(attempts[1])}) AND resolution_ms={resolution} AND bucket_index={bucket} FORMAT ArrowStream"
        technical = _arrow(self.client,sql,('bucket_index','resolution_ms','macd_line','macd_signal'),
                           (pa.uint32(),pa.uint32(),pa.float64(),pa.float64()),{bucket})
        if len(bars) != 1 or len(technical) != 1 or bars[0][1] != resolution or technical[0][1] != resolution or bars[0][3] not in (0,1):
            raise ValueError("Declared management completed source is missing or malformed")
        quote_bucket=(boundary_ms+SESSION_OPEN_OFFSET_MS)//self.market.execution_interval.milliseconds-1
        sql=assert_select_only(f"SELECT ticker,bucket_index,toString(attempt_id) AS liquidity_attempt_id,bid_int,ask_int,quote_timestamp_us,quote_valid FROM arte.liquidity_100ms_v1 WHERE {common} AND (ticker,bucket_index,attempt_id) IN (({_literal(ticker)},{quote_bucket},toUUID({_literal(attempts[2])}))) SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow")
        rows=[json.loads(line) for line in self.client.execute(sql).splitlines() if line.strip()]
        names={'ticker','bucket_index','liquidity_attempt_id','bid_int','ask_int','quote_timestamp_us','quote_valid'}
        if len(rows)!=1 or type(rows[0]) is not dict or set(rows[0])!=names:
            raise ValueError("Declared management quote coverage is incomplete or ambiguous")
        q=rows[0]
        now=exact_epoch_us(market_day_boundary(native['session_date'],boundary_ms))
        if (q['ticker']!=ticker or type(q['bucket_index']) is not int or q['bucket_index']!=quote_bucket
                or q['liquidity_attempt_id']!=attempts[2]
                or any(type(q[n]) is not int for n in ('bid_int','ask_int','quote_timestamp_us','quote_valid'))
                or q['quote_valid'] not in (0,1) or not 0<=q['quote_timestamp_us']<=now):
            raise ValueError("Declared management quote differs from exact causal source")
        # NaN denotes producer unavailability; infinities are corrupt, not values.
        values=technical[0][2:]
        if any(v in (float('inf'),float('-inf')) for v in values):
            raise ValueError("Declared management MACD source is nonfinite")
        value=DeclaredCompletedProducerFacts(self.run_id,envelope['payload_hash'],self.market.token,
            self.market.build_id,tuple(attempts),native['session_date'],ticker,boundary_ms,resolution,
            clock,int(bars[0][2]),bool(bars[0][3]),*(v if isfinite(v) else None for v in values),
            q['bid_int'],q['ask_int'],q['quote_timestamp_us'],bool(q['quote_valid']),'')
        return DeclaredCompletedProducerFacts(**value.payload(),content_hash=_digest(value.payload()))

    def compare_completed_facts(self, claimed):
        if type(claimed) is not DeclaredCompletedProducerFacts:
            raise ValueError("Declared management needs exact producer fact type")
        expected=self.completed_producer_facts(claimed.ticker,claimed.boundary_ms)
        if canonical_json(claimed.payload())!=canonical_json(expected.payload()) or claimed.content_hash!=expected.content_hash:
            raise ValueError("Declared management producer fields or content hash differ")
        return expected

    def verify_historical_predecessor(self, requested):
        """Do not reinterpret an old prefix or caller digest as own authority."""
        if type(requested) is not DeclaredHistoricalPredecessor:
            raise ValueError("Declared historical scope requires exact typed identity")
        requested.__post_init__()
        native, _, envelope, _ = self._binding()
        if (requested.run_id != self.run_id or requested.configuration_hash != envelope['payload_hash']
                or requested.market_plan_token != native['market_plan_token']):
            raise ValueError("Declared historical scope has foreign run/configuration/source")
        raise ValueError("Declared own-family installed V4 predecessor/source hook is missing; no historical financial authority")

    def reconstruct_manager_sources(self, state):
        """Source equivalence only; held/filled/trailing state is not attested."""
        from src.backend.backtest_declared_native_fixed_management import DeclaredManagementState
        if type(state) is not DeclaredManagementState or state.run_id != self.run_id:
            raise ValueError("Declared manager image has foreign type/run")
        loaded=self.reload_entry_plan()
        if (type(state.boundary_ms) is not int or state.boundary_ms<0
                or state.boundary_ms>loaded[2]['definition']['end_local_ms']-SESSION_OPEN_OFFSET_MS
                or state.boundary_ms%100
                or type(state.submitted) is not tuple or len(state.submitted)>4096
                or state.source_token!=loaded[0].token):
            raise ValueError("Declared manager source image differs")
        keys=[];result=[]
        for item in state.submitted:
            if type(item) is not tuple or len(item)!=2 or type(item[1]) is not DeclaredNativeFixedEntryProposal:
                raise ValueError("Declared manager submitted image has invalid shape")
            key,entry=item
            if (key!=(entry.account_id,entry.assignment_id,entry.ticker)
                    or entry.boundary_ms>state.boundary_ms):
                raise ValueError("Declared manager entry has foreign identity or future clock")
            keys.append(key);result.append(self._entry_facts(entry,loaded))
        if keys!=sorted(set(keys)):
            raise ValueError("Declared manager entry image is ambiguous or unordered")
        return tuple(result)

    def verify_manager_state(self, state):
        self.reconstruct_manager_sources(state)
        raise ValueError("Declared own-family installed V4 manager predecessor/source hook is missing; restore remains closed")
