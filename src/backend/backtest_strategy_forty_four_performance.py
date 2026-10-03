"""Strategy 44 positions grouped by certified acquisition intent, not ticker.

Broker inventory remains aggregated by instrument. This read-only projection
assigns each execution to its exact native entry leg before deriving lifecycles.
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.performance import derive_trade_episodes, derive_position_lifecycles


def derive_leg_positions(executions, ownership, orders=(), order_ownership=None):
    groups = defaultdict(list)
    for execution in executions:
        if (execution.strategy_id, execution.strategy_revision) != ("squeeze-grid-strategy",44):
            raise ValueError("Strategy 44 position projection received a foreign execution")
        leg = ownership.get(execution.execution_id)
        if not leg:
            raise ValueError("Strategy 44 execution lacks its exact acquisition leg")
        groups[(execution.account_id, leg)].append(execution)
    episodes, lifecycles = [], []
    for (account, leg), fills in sorted(groups.items()):
        if len({(e.instrument.instrument_id,e.instrument.conid) for e in fills}) != 1:
            raise ValueError("Strategy 44 acquisition crosses instruments")
        inventory = Decimal(0)
        for fill in sorted(fills,key=lambda e:(e.source_event_time,e.journal_sequence or 0,e.execution_id)):
            inventory += fill.quantity * (1 if fill.side == "BUY" else -1)
            if inventory < 0:
                raise ValueError("Strategy 44 leg sells more than its acquired quantity")
        closed = derive_trade_episodes(fills)
        owned_orders = tuple(order for order in orders if order.account_id == account
            and (order_ownership or {}).get((account,order.broker_order_id)) == leg)
        rows = derive_position_lifecycles(fills,owned_orders)
        if len(rows) != 1:
            raise ValueError("Strategy 44 acquisition reopened after exit")
        identity = str(uuid5(NAMESPACE_URL,f"strategy44-position:{fills[0].run_id}:{account}:{leg}"))
        episodes.extend(replace(row,episode_id=identity) for row in closed)
        for row in rows:
            row.update(lifecycle_id=identity,episode_id=identity,entry_intent_id=leg)
        lifecycles.extend(rows)
    lifecycles.sort(key=lambda row:(row["opened_at"],row["lifecycle_id"]),reverse=True)
    return episodes,lifecycles


def derive_saved_leg_positions(client,prefix,executions):
    from .backtest_v4_saved_review import _complete_detail_rows
    from src.trading_runtime.arte_journal_writer import (
        load_committed_order_command_page,load_committed_order_context_page)
    from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
    wanted = {(e.account_id,e.client_order_id) for e in executions}
    commands = [row for row in _complete_detail_rows(load_committed_order_command_page,client,prefix)
                if (row["account_id"],row["client_order_id"]) in wanted]
    contexts, sources = {},{}
    for offset in range(0,len(commands),500):
        page = tuple(commands[offset:offset+500])
        joined = load_committed_order_context_page(client,prefix,page,include_source=True)
        contexts.update(joined)
        ids = tuple(sorted({row["source_intent_record_id"] for row in joined.values()}))
        recovered = load_committed_strategy_intent_page(client,prefix,record_ids=ids,limit=500,
                                                       include_source_batch=True)
        sources.update({row.record_id:row for row in recovered})
    by_command = {}
    for command in commands:
        key = (command["account_id"],command["client_order_id"])
        context = contexts[command["record_id"]]
        source = sources[context["source_intent_record_id"]]
        intent = source.intent
        if intent.reason == "strategy_forty_four_entry":
            leg = intent.intent_id
        elif intent.reason in {"strategy_forty_four_adaptive_stop","strategy_forty_four_session_liquidation"}:
            event, = source.source_batch.events
            leg = event["correlation_id"]
        else:
            raise ValueError("Strategy 44 position command has a foreign source")
        if key in by_command:
            raise ValueError("Strategy 44 position repeats an order command")
        by_command[key] = (leg,command,source)
    ownership = {}
    for execution in executions:
        leg,command,source = by_command[(execution.account_id,execution.client_order_id)]
        if (command["ticker"] != execution.instrument.symbol
                or int(command["conid"]) != execution.instrument.conid
                or source.account_id != execution.account_id
                or not source.sequence < int(command["sequence"]) < execution.journal_sequence):
            raise ValueError("Strategy 44 position command differs from its execution")
        ownership[execution.execution_id] = leg
    return derive_leg_positions(executions,ownership)


def derive_runtime_leg_positions(snapshot):
    owners = {(account,broker_id):entry for account,broker_id,entry
              in getattr(snapshot,"leg_order_ownership",())}
    ownership = {e.execution_id:owners.get((e.account_id,e.broker_order_id))
                 for e in snapshot.executions}
    return derive_leg_positions(snapshot.executions,ownership,snapshot.orders,owners)
