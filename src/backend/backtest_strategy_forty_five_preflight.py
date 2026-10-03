"""App preflight for the bounded, independent Strategy 45 comparison."""
from datetime import date, time
from contextlib import closing

from .backtest_strategy_forty_five_configuration import certify_configuration
from .backtest_strategy_forty_five_controller import certify_inputs, reader_factory
from .backtest_strategy_forty_five_certification import certify_projection
from .historical_runtime_versions import backend_source_fingerprint, LOADED_BACKEND_FINGERPRINT


def preflight(*, anchor_date, session_count, initial_cash, start_time, end_time,
              tickers, configuration_revision, saved_review_authority=None):
    from .replay_run_service import historical_window_preview
    window = historical_window_preview(mode="backtest", anchor_date=anchor_date,
        session_count=session_count, replay_end_date=None)
    checks = []
    market, causal = {}, {}
    def check(identity, function):
        try:
            result = function()
            checks.append(dict(id=identity, label=identity.replace("_", " "),
                status="ready", required=True, summary="Verified", evidence=result if isinstance(result, str) else ""))
            return result
        except (ValueError, RuntimeError, OSError) as exc:
            checks.append(dict(id=identity, label=identity.replace("_", " "),
                status="blocked", required=True, summary=str(exc), evidence=""))
            return None
    def exact_window():
        if (window["sessions"] != ["2026-09-03"] or session_count != 1
                or initial_cash != 10_000 or start_time != time(4) or end_time != time(9, 30)
                or tickers or saved_review_authority is not None):
            raise ValueError("Strategy 45 comparison requires September 3 full premarket, $10,000, complete certified population and a new run")
        window.update(start="2026-09-03T04:00:00-04:00", end="2026-09-03T09:30:00-04:00",
                      source="arte_persisted_market_day")
        return "2026-09-03 premarket; LGHL excluded by the immutable policy"
    check("session_window", exact_window)
    def code():
        manifest = configuration_revision["payload"]["strategy"]["numbered_release"]
        current = backend_source_fingerprint()
        if current != LOADED_BACKEND_FINGERPRINT or current != manifest["approved_code_fingerprint"]:
            raise RuntimeError("Strategy 45 approved source differs from the loaded backend; deploy and restart the approved source")
        return certify_projection()
    check("runtime_versions", code)
    def release():
        with closing(reader_factory()) as reader:
            certified = certify_configuration(reader)
        if (certified.payload_hash != configuration_revision.get("content_hash")
                or certified.revision()["revision_id"] != configuration_revision.get("revision_id")):
            raise RuntimeError("Strategy 45 selected release differs from its immutable catalog")
        return certified.token
    check("immutable_configuration", release)
    if all(row["status"] == "ready" for row in checks):
        inputs = check("certified_sources", lambda: certify_inputs(configuration_revision["payload"], date(2026, 9, 3)))
        if inputs is not None:
            plan, history, execution, prices = inputs
            market = {**plan.market.payload(), "strategy_forty_five_history_token": history.token,
                "strategy_forty_five_identity_token": plan.identity.token,
                "strategy_forty_five_structure_token": plan.structure.token,
                "strategy_forty_five_price_token": prices.token,
                "strategy_forty_five_liquidity_token": history.liquidity_book.token,
                "price_level_plan_token": prices.token,
                "price_level_unit_count": len(prices.units),
                "parent_market_plan_token": plan.market.token,
                "execution_market_plan_token": execution.token,
                "squeeze_ticker_count": len(plan.tickers)}
            causal = dict(build_id=plan.market.build_id, token=plan.structure.token,
                          catalog_hash=plan.structure.token)
    ready = bool(market) and all(row["status"] == "ready" for row in checks)
    return dict(schema_version=1, mode="backtest", window=window, checks=checks,
        ready=ready, strategy_run_ready=ready, coverage={}, gateway=dict(source="arte_persisted_market_day"),
        configuration_revision_id=configuration_revision.get("revision_id", ""),
        configuration_revision=45, configuration_content_hash=configuration_revision.get("content_hash", ""),
        configuration_label="Strategy 45", run_plan_id=configuration_revision.get("run_plan_id", ""),
        available_run_plans=configuration_revision.get("available_run_plans", []),
        execution_interval="100ms", market_data_plan=market, causal_v7_plan=causal,
        historical_watchlist_plans=[], initial_cash=initial_cash,
        experiment_start_time=start_time.isoformat(timespec="seconds"),
        experiment_end_time=end_time.isoformat(timespec="seconds"))
