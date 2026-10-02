"""Prepared independent Strategy37 prefix readback; no journal admission yet."""
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from .backtest_strategy_episode_activity_gate import EpisodeActivityStaticGate
from .backtest_strategy_certified_price_break import _source_parent_number
from src.trading_runtime.arte_entry_activity_v4 import EntryActivityAuthority, activity_event_instant
from src.trading_runtime.strategy_entry_activity_witness import validate_entry_activity_witness


@dataclass(frozen=True, slots=True)
class EpisodeActivityReadbackAuthority:
    """Bind an independently compiled full prefix to one immutable run.

    Strategy37's activity_source_token seals the full episode gate, which itself
    incorporates Strategy36's native candle-source token. The remaining typed
    fields and four completed candle columns retain their existing semantics.
    Journal rows supply identity only, never an admission mask or prefix state.
    """
    run_id: str
    gate: EpisodeActivityStaticGate

    def __post_init__(self):
        if (type(self.run_id) is not str or not self.run_id
                or type(self.gate) is not EpisodeActivityStaticGate):
            raise ValueError('Episode activity readback requires exact run and certified gate')
        _source_parent_number(self.gate.activity.parent, 35)

    @property
    def plan(self):
        return self.gate.activity

    def witness(self, ticker, boundary_ms):
        native, _, token = self.gate.admission_witness(ticker, boundary_ms)
        return validate_entry_activity_witness(replace(native, activity_source_token=token))

    def resolve(self, run_id, entries, intents):
        if run_id != self.run_id:
            raise ValueError('Episode activity readback differs from certified run')
        parents = {row['record_id']: row for row in intents}
        if len(parents) != len(intents):
            raise ValueError('Episode activity readback has duplicate intent parents')
        result, seen = [], set()
        for row in entries:
            if row['strategy_number'] != 37:
                raise ValueError('Episode activity readback received another numbered entry')
            parent = row['parent_record_id']
            intent = parents.get(parent)
            if (type(row['strategy_number']) is not int or parent in seen or intent is None
                    or row['run_id'] != run_id or intent['run_id'] != run_id
                    or row['batch_id'] != intent['batch_id']
                    or row['event_month'] != intent['event_month']
                    or intent['action'] != 'enter_long' or intent['reason'] != 'strategy_one_entry'):
                raise ValueError('Episode activity readback has unrelated entry or intent scope')
            key = (intent['ticker'], row['boundary_ms'])
            witness = self.witness(*key)
            _, episode_start, _ = self.gate.admission_witness(*key)
            month = activity_event_instant(witness).date().replace(day=1).isoformat()
            identity = (f"strategy-37:{witness.session_date}:{row['assignment_id']}:"
                        f"{intent['account_id']}:{witness.ticker}:{witness.boundary_ms}:{episode_start}")
            if (row['episode_start_ms'] != episode_start or str(row['event_month']) != month
                    or intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity))):
                raise ValueError('Episode activity readback differs from native episode or intent identity')
            result.append(EntryActivityAuthority(parent, witness, episode_start))
            seen.add(parent)
        return tuple(result)
