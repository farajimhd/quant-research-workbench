"""Cold Strategy 44 entry geometry, sizing and producer-reference audit."""
from contextlib import closing
from collections import defaultdict
from dataclasses import replace
from datetime import date
from math import log1p
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from .backtest_strategy_forty_four_configuration import certify_configuration
from .backtest_strategy_forty_four_controller import certify_inputs, reader_factory
from .backtest_strategy_forty_four_facts import load_admission_facts
from .backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_forty_four_rules import propose_batch, entry_intents


def audit_terminal_source(client, prefix, context):
    from src.trading_runtime.arte_backtest_definition import load_backtest_definition
    from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
    if (context["strategy_id"], context["strategy_revision"]) != ("squeeze-grid-strategy", 44):
        raise ValueError("Strategy 44 cold source crossed a foreign run")
    with closing(reader_factory()) as reader:
        release = certify_configuration(reader)
    if release.payload_hash != context["configuration_hash"]:
        raise ValueError("Strategy 44 cold run differs from its immutable release")
    saved = load_backtest_definition(client, prefix.run_id, run_context=context)
    definition = saved["definition"]
    if (str(context["session_date"]) != "2026-09-03" or definition["start_local_ms"] != 14_400_000
            or definition["end_local_ms"] != 34_200_000 or float(definition["initial_cash"]) != 10_000
            or saved["tickers"] or saved["assignments"]):
        raise ValueError("Strategy 44 saved comparison window or population changed")
    plan, history, execution, prices = certify_inputs(release.payload, date(2026,9,3))
    price_pin = saved["price_plan"]
    if (context["market_plan_token"] != plan.market.token or price_pin is None
            or price_pin["price_plan_token"] != prices.token
            or price_pin["unit_count"] != len(prices.units)):
        raise ValueError("Strategy 44 cold market or native price authority changed")
    with closing(reader_factory()) as reader:
        facts = {row.ticker: row for row in load_admission_facts(plan, history, reader, price_plan=prices)}
    entries, management = defaultdict(list), []
    after = 0
    while True:
        page = load_committed_strategy_intent_page(client, prefix, after_sequence=after,
            limit=500, include_source_batch=True)
        if not page:
            break
        for source in page:
            intent = source.intent
            if intent.reason == "strategy_forty_four_entry":
                entries[(source.account_id, intent.ticker)].append(source)
            elif intent.reason in {"strategy_forty_four_adaptive_stop", "strategy_forty_four_session_liquidation"}:
                management.append(source)
            else:
                raise ValueError("Strategy 44 cold run contains a foreign trading intent")
        after = page[-1].sequence
    original = {}
    for (account, ticker), sources in entries.items():
        if len(sources) != 15 or account not in context["account_ids"]:
            raise ValueError("Strategy 44 cold batch is incomplete or repeated")
        assignment = str(uuid5(NAMESPACE_URL, f"strategy44-assignment:{prefix.run_id}:{ticker}"))
        batch = propose_batch(facts[ticker], account_id=account, assignment_id=assignment,
            free_cash_after_reservations=10_000, already_submitted=False)
        if batch is None:
            raise ValueError("Strategy 44 cold entry violates its certified signal geometry")
        templates = entry_intents(batch, session_date=date(2026,9,3))
        by_id = {row.intent.intent_id: row for row in sources}
        if set(by_id) != {row.intent_id for row in templates}:
            raise ValueError("Strategy 44 cold batch lost its ordinal or submission-lock identity")
        weights = tuple(1/log1p(rank) for rank in range(1,16))
        total, lower, upper = sum(weights), 0., float("inf")
        for leg, template, weight in zip(batch.legs, templates, weights):
            source = by_id[template.intent_id]
            quantity = source.intent.quantity
            if quantity <= 0 or not float(quantity).is_integer():
                raise ValueError("Strategy 44 cold entry quantity is not integral")
            expected = replace(template, quantity=quantity, capital_request=replace(
                template.capital_request, value=quantity, minimum_quantity=quantity, maximum_quantity=quantity))
            if source.intent != expected:
                raise ValueError("Strategy 44 cold entry differs from its causal target, stop or policy")
            cost = leg.limit_price + .01
            lower = max(lower, quantity*cost*total/weight)
            upper = min(upper, (quantity+1)*cost*total/weight)
            original[template.intent_id] = (account, ticker)
        if lower >= upper:
            raise ValueError("Strategy 44 cold quantities cannot share its approved decreasing budget")
    for source in management:
        event, = source.source_batch.events
        entry_id = event["correlation_id"]
        if original.get(entry_id) != (source.account_id, source.intent.ticker):
            raise ValueError("Strategy 44 cold management has no exact owned acquisition")
        delta = source.intent.event_time - market_day_boundary("2026-09-03", 0)
        boundary = (delta.days*86400 + delta.seconds)*1000 + delta.microseconds//1000
        pointer = SimpleNamespace(ticker=source.intent.ticker, boundary_ms=boundary, source_token=history.token)
        if event["causation_id"] != history.fact_id(pointer):
            raise ValueError("Strategy 44 cold management lost its completed producer fact")
        if source.intent.action == "exit" and not plan.session_end_ms - 60_000 <= boundary <= plan.session_end_ms:
            raise ValueError("Strategy 44 cold liquidation left its pinned cutoff")
    return dict(history_token=history.token, ticker_batches=len(entries), entry_intents=len(original),
                management_intents=len(management), entry_geometry_and_decreasing_sizing_verified=True)
