"""Independent arithmetic/reference checks for local greedy supervision."""
import math
import random
from io import StringIO

import polars as pl
import pytest
from rich.console import Console

from scripts.build_hindsight_greedy import (
    illustrative_row, summarize, merge_summary, flat_policy, report_table,
    verify_files, phase1_plan,
)
from src.market_engine.hindsight_greedy import coefficients, ActionTable, Position


def phase1_rows():
    rows = []
    for t in (1_000_000, 2_000_000, 3_000_000):
        row = dict(time_us=t, ticker="B", listing_id="listing:B", quote_valid=True,
                   bid=10., ask=11., bid_size=100., ask_size=100.)
        for side in ("long", "short"):
            row.update({f"{side}_status": "available", f"{side}_target_us": t+3_500_000,
                        f"{side}_target_id": 1, f"{side}_available_us": t+5_000_000,
                        f"{side}_hold_seconds": 3.5, f"{side}_bid": 16., f"{side}_ask": 7.})
        rows.append(row)
    return pl.DataFrame(rows)


def bd():
    return ActionTable([illustrative_row("B", 10, 6, 3, .99),
                        illustrative_row("D", 55, 55, 4, .99)],
                       [Position("B", "long", 5, 10, 10)], mode="long")


def test_user_abc_example_discount_and_sizes():
    state = ActionTable([illustrative_row("A", 50, 5, 4, .99),
                         illustrative_row("B", 10, 6, 6, .99),
                         illustrative_row("C", 2, .5, 10, .99)], [], mode="long")
    assert state.cash == 50
    results = [state.evaluate({key: size}) for key, size in [("A:long", 1), ("B:long", 5), ("C:long", 25)]]
    assert [r["undiscounted_future_profit"] for r in results] == [5, 30, 12.5]
    assert results[1]["discounted_future_value"] == pytest.approx(30*.99**6)
    assert max(range(3), key=lambda i: results[i]["discounted_future_value"]) == 1


@pytest.mark.parametrize("changes,profit,increment", [
    ({}, 30, 0), ({"B:long": .5}, 33, 3*.99**3),
    ({"D:long": 1/11}, 35, 5*.99**4),
    ({"B:long": -2.5, "D:long": 6/11}, 45, 30*.99**4-15*.99**3),
    ({"B:long": -5, "D:long": 1}, 55, 55*.99**4-30*.99**3),
    ({"B:long": -1}, 24, -6*.99**3), ({"B:long": -5}, 0, -30*.99**3),
])
def test_user_bd_all_action_types(changes, profit, increment):
    state = bd()
    assert state.open_cost == 50 and state.cash == 5 and state.budget == 55
    result = state.evaluate(changes)
    assert result["feasible"]
    assert result["undiscounted_future_profit"] == pytest.approx(profit)
    assert result["delta_vs_hold"] == pytest.approx(increment)


def test_exhaustive_fractional_grid_matches_independent_cashflow_oracle():
    state = bd()
    best = (-math.inf, None)
    # Every half-share B / tenth-share D allocation in a bounded reference grid.
    for b in range(12):
        for d in range(12):
            bq, dq = b/2, d/10
            result = state.evaluate({"B:long": bq-5, "D:long": dq})
            feasible = 10*bq + 55*dq <= 55+1e-9
            assert result["feasible"] == feasible
            if feasible:
                expected = bq*6*.99**3 + dq*55*.99**4
                assert result["discounted_future_value"] == pytest.approx(expected)
                assert result["cash_after"] == pytest.approx(55-10*bq-55*dq)
                if expected > best[0]:
                    best = expected, (bq, dq)
    assert best[1] == (0, 1)


def test_many_random_fractional_sizes_not_only_displayed_examples():
    rng = random.Random(19)
    state = bd()
    for _ in range(300):
        d = rng.random()
        b = rng.random()*(55-55*d)/10
        result = state.evaluate({"B:long": b-5, "D:long": d})
        assert result["feasible"]
        assert result["discounted_future_value"] == pytest.approx(b*6*.99**3+d*55*.99**4)


def test_no_refunding_while_evaluating_and_transaction_order_independent():
    state = bd()
    a = state.evaluate({"B:long": -5, "D:long": 1})
    b = state.evaluate({"D:long": 1, "B:long": -5})
    assert a["cash_after"] == b["cash_after"] == 0
    assert state.cash == 5
    assert not state.evaluate({"B:long": 1})["feasible"]
    assert not state.evaluate({"B:long": -6})["feasible"]
    assert not state.evaluate({"D:long": -1e-12})["feasible"]


def test_spread_and_cost_are_not_charged_again_to_retained_shares():
    frame = coefficients(phase1_rows(), .99, .1)
    long = frame.filter(pl.col("side") == "long").row(0, named=True)
    assert long["entry_price"] == pytest.approx(11.1)
    assert long["close_price"] == pytest.approx(9.9)
    assert long["open_profit_per_share"] == pytest.approx(4.8)
    assert long["hold_profit_per_share"] == pytest.approx(6)
    assert long["discount"] == pytest.approx(.99**3.5)
    short = frame.filter(pl.col("side") == "short").row(0, named=True)
    assert short["open_profit_per_share"] == pytest.approx(2.8)
    assert short["hold_profit_per_share"] == pytest.approx(4)
    state = ActionTable([long], [Position("B", "long", .5, 8, 8)], mode="long")
    result = state.evaluate({"B:long": -.5})
    assert result["realized_pnl_now"] == pytest.approx(.95)
    assert result["discounted_future_value"] == 0
    assert result["cash_after"] == pytest.approx(state.cash + .5*9.9)


def test_short_release_locks_sale_proceeds_and_no_double_count():
    rows = [illustrative_row("S", 10, 3, 4, .99, "short")]
    state = ActionTable(rows, [], mode="short")
    opened = state.evaluate({"S:short": 1})
    assert opened["cash_after"] == 0
    assert not state.evaluate({"S:short": 2})["feasible"]
    rows[0]["close_price"] = 8
    held = ActionTable(rows, [Position("S", "short", 1, 10, 10)], mode="short")
    closed = held.evaluate({"S:short": -1})
    assert closed["cash_after"] == 12
    assert closed["realized_pnl_now"] == 2


def test_forced_insolvent_short_liquidation_records_deficit_without_refunding():
    row = illustrative_row('S',25,0,0,1,'short')
    row.update(can_open=False,session_terminal=True,value_available=False)
    state = ActionTable([row],[Position('S','short',1,10,10)],mode='short')
    closed = state.evaluate({'S:short':-1})
    assert closed['feasible'] and closed['settlement_status'] == 'insolvent'
    assert closed['cash_after'] == -5 and closed['cash_deficit'] == 5
    assert closed['realized_pnl_now'] == -15 and closed['resulting_shares']['S:short'] == 0
    assert closed['discounted_future_value'] == 0
    assert not state.evaluate({})['feasible']
    assert not state.evaluate({'S:short':-.5})['feasible']
    assert not state.evaluate({'S:short':1})['feasible']
    assert not state.evaluate({'S:short':-2})['feasible']
    assert state.cash == 0  # No capital injection or state mutation.
    output = StringIO()
    Console(file=output,width=100,color_system=None).print(report_table(dict(state=state.describe(),actions=[
        dict(name='Liquidate',changes={'S:short':-1},result=closed)])))
    assert 'insolvent' in output.getvalue() and '$5.0000' in output.getvalue()
    row['session_terminal'] = False
    assert not ActionTable([row],[Position('S','short',1,10,10)],mode='short').evaluate({'S:short':-1})['feasible']


def test_hold_value_does_not_require_new_entry_and_does_not_enter_market_ranking():
    frame = phase1_rows().with_columns(pl.lit(.05).alias('decision_price'),pl.lit(True).alias('price_valid'),
        pl.lit(.02).alias('long_target_price'),pl.lit(.02).alias('short_target_price'))
    values = coefficients(frame,1,.1,valuation_basis='price_action').filter(pl.col('side') == 'short')
    row = values.row(0,named=True)
    assert not row['can_open'] and not row['open_value_available'] and not row['value_available']
    assert row['can_close'] and row['hold_value_available']
    assert row['open_value_per_share'] is None
    assert row['hold_value_per_share'] == pytest.approx(.03)
    state = ActionTable([row],[Position('B','short',1,1,1)],mode='short')
    assert state.evaluate({})['discounted_future_value'] == pytest.approx(.03)
    assert state.evaluate({'B:short':-.5})['discounted_future_value'] == pytest.approx(.015)
    assert state.evaluate({'B:short':-1})['feasible']
    assert not state.evaluate({'B:short':.5})['feasible']
    assert flat_policy(summarize(values,'short'))['chosen_action'].to_list() == ['wait']*3
    unknown = coefficients(frame.with_columns(pl.lit('no_future_macd_target').alias('short_status')),
                           1,.1,valuation_basis='price_action').filter(pl.col('side') == 'short').row(0,named=True)
    assert not unknown['hold_value_available']


def test_completed_activity_gates_opening_but_preserves_holding_and_closing():
    frame = phase1_rows().with_columns(
        pl.lit(10.).alias('decision_price'),pl.lit(True).alias('price_valid'),
        pl.lit(12.).alias('long_target_price'),pl.lit(8.).alias('short_target_price'),
        pl.Series('volume_60s',[0.,19_999.,20_000.]),pl.Series('trades_60s',[0,11,11]))
    values = coefficients(frame,valuation_basis='price_action',min_volume_60s=20_000.,
        min_trades_60s=11).filter(pl.col('side')=='long')
    assert values['can_open'].to_list() == [False,False,True]
    assert values['can_close'].to_list() == [True]*3
    assert values['hold_value_available'].to_list() == [True]*3
    assert values['status'].to_list() == ['liquidity_below_threshold']*2+['available']
    assert values['open_value_per_share'].to_list()[:2] == [None,None]
    assert values['hold_value_per_share'][0] is not None
    with pytest.raises(ValueError,match='completed one-minute activity'):
        coefficients(frame.drop('volume_60s'),valuation_basis='price_action',min_volume_60s=20_000.)
    with pytest.raises(ValueError,match='requires arte price-action'):
        coefficients(phase1_rows(),min_trades_60s=11)


def test_negative_after_cost_target_remains_a_known_loss():
    frame = phase1_rows().with_columns(pl.lit(.05).alias('decision_price'),pl.lit(True).alias('price_valid'),
        pl.lit(.02).alias('long_target_price'),pl.lit(.02).alias('short_target_price'))
    row = coefficients(frame,1,.1,valuation_basis='price_action').filter(pl.col('side') == 'long').row(0,named=True)
    assert row['open_value_available'] and row['hold_value_available']
    assert row['open_profit_per_share'] == pytest.approx(-.23)
    assert row['hold_profit_per_share'] == pytest.approx(-.03)


@pytest.mark.parametrize('resolution',[.1,1.,5.,60.])
def test_discount_scales_with_macd_resolution_not_realized_episode_length(resolution):
    from src.market_engine.hindsight_greedy import discount_policy
    policy = discount_policy(resolution)
    assert policy['half_life_bars'] == 30 and policy['half_life_seconds'] == 30*resolution
    frame = phase1_rows().with_columns(pl.lit(30*resolution).alias('long_hold_seconds'),
                                     pl.lit(60*resolution).alias('short_hold_seconds'))
    result = coefficients(frame,macd_resolution_seconds=resolution)
    assert result.filter(pl.col('side') == 'long')['discount'].to_list() == pytest.approx([.5]*3)
    assert result.filter(pl.col('side') == 'short')['discount'].to_list() == pytest.approx([.25]*3)
    assert discount_policy(resolution,gamma=.99)['gamma_per_second'] == .99


@pytest.mark.parametrize('kwargs',[{'half_life_bars':0},{'half_life_bars':float('nan')},
    {'half_life_bars':30,'gamma':.99},{'macd_resolution_seconds':0},{'macd_resolution_seconds':float('inf')}])
def test_invalid_discount_configuration_fails(kwargs):
    from src.market_engine.hindsight_greedy import discount_policy
    with pytest.raises(ValueError): discount_policy(**kwargs)


def test_three_modes_and_reversal_require_close_of_other_side():
    rows = [illustrative_row("B", 10, 3, 4, .99, side) for side in ("long", "short")]
    for mode, keys in [("long", {"B:long"}), ("short", {"B:short"}), ("long_short", {"B:long", "B:short"})]:
        assert set(ActionTable(rows, [], mode=mode).rows) == keys
    state = ActionTable(rows, [Position("B", "long", 1, 10, 10)])
    assert state.evaluate({"B:long": -1, "B:short": 1})["feasible"]
    assert not state.evaluate({"B:long": -.5, "B:short": .5})["feasible"]


def test_missing_labels_never_turn_into_zero_or_change_budget():
    frame = phase1_rows().with_columns(pl.lit("target_quote_unavailable").alias("long_status"))
    values = coefficients(frame)
    row = values.filter(pl.col("side") == "long").row(0, named=True)
    assert row["can_open"] and not row["value_available"]
    state = ActionTable([row], [Position("B", "long", 1, 11, 11)], mode="long")
    assert state.budget == 11
    assert state.evaluate({})["value_status"] == "unavailable"
    exited = state.evaluate({"B:long": -1})
    assert exited["discounted_future_value"] == 0 and exited["delta_vs_hold"] is None
    policy = flat_policy(summarize(values, "long"))
    assert policy["chosen_action"].null_count() == policy.height


def test_merged_market_table_matches_direct_all_listing_reduction_and_ties():
    a = coefficients(phase1_rows())
    b = a.with_columns(pl.lit("A").alias("ticker"), pl.lit("listing:A").alias("listing_id"))
    for mode in ("long", "short", "long_short"):
        merged = merge_summary(summarize(a, mode), summarize(b, mode))
        direct = summarize(pl.concat([a, b]), mode)
        assert merged.to_dicts() == direct.to_dicts()
        assert all(key.startswith("A:") for key in merged["best_key"])


def test_all_negative_chooses_wait_and_unknown_does_not_get_ranked():
    values = coefficients(phase1_rows()).with_columns(pl.lit(-1.).alias("open_value_per_dollar"))
    policy = flat_policy(summarize(values, "long_short"))
    assert policy["chosen_action"].to_list() == ["wait"]*3
    assert policy["discounted_value"].to_list() == [0.]*3


@pytest.mark.parametrize("gamma,cost", [(0, 0), (1.1, 0), (float("nan"), 0), (.99, -1), (.99, float("inf"))])
def test_bad_parameters_fail(gamma, cost):
    with pytest.raises(ValueError):
        coefficients(phase1_rows(), gamma, cost)


def test_integrity_fails_and_no_missing_completion_fallback(tmp_path):
    (tmp_path/"file").write_text("bad")
    with pytest.raises(ValueError, match="integrity"):
        verify_files(tmp_path, {"file": "wrong"})
    with pytest.raises(FileNotFoundError):
        phase1_plan(tmp_path)


def test_narrow_plain_terminal_shows_action_sizes_and_value_units():
    state = bd()
    report = dict(state=state.describe(), actions=[dict(name="Add B", changes={"B:long": .5},
                                                       result=state.evaluate({"B:long": .5}))])
    out = StringIO()
    Console(file=out, width=80, color_system=None).print(report_table(report))
    assert "Share changes" in out.getvalue() and "+0.5" in out.getvalue() and "32.0199" in out.getvalue()
    assert "\x1b[" not in out.getvalue()


def test_runnable_builder_resume_stop_and_corrupt_source(tmp_path, monkeypatch):
    from datetime import date
    from scripts.build_hindsight_greedy import main, file_hash
    from src.market_engine.hindsight_phase1 import bounds, digest
    from src.market_engine.level_book_store import write, read
    monkeypatch.setenv("QW_RUNTIME_ROOT", str(tmp_path))
    source = tmp_path / "source"
    listing = dict(ticker="B", listing_id="listing:B")
    second = dict(ticker="C", listing_id="listing:C")
    plan = dict(version="hindsight-phase1-macd-v1", date="2026-08-21",
                selected=[listing,second], scope="explicit_canary")
    plan["plan_hash"] = digest(plan)
    write(source/"plan.json", plan)
    write(source/"complete.json", dict(plan_hash=plan["plan_hash"], listing_count=2, rows=115202))
    folder = source/"listings"/digest(listing)[:20]
    folder.mkdir(parents=True)
    left, right = bounds(date(2026, 8, 21))
    base = phase1_rows().head(1).drop("time_us")
    frame = pl.DataFrame({"time_us": range(left, right+1, 1_000_000)}).join(base, how="cross")
    for side in ("long", "short"):
        frame = frame.with_columns(
            pl.lit(left+3_500_000).alias(side+"_target_us"),
            pl.lit(left+5_000_000).alias(side+"_available_us"),
            ((left+3_500_000-pl.col("time_us"))/1e6).alias(side+"_hold_seconds"),
            pl.when(pl.col("time_us") < left+3_500_000).then(pl.lit("available"))
            .otherwise(pl.lit("no_future_macd_target")).alias(side+"_status"))
    frame.write_parquet(folder/"opportunities.parquet")
    write(folder/"ready.json", dict(plan_hash=plan["plan_hash"], listing=listing, rows=57601,
                                    files={"opportunities.parquet": file_hash(folder/"opportunities.parquet")}))
    other = source/"listings"/digest(second)[:20]
    other.mkdir(parents=True)
    frame.with_columns(pl.lit('C').alias('ticker'),pl.lit('listing:C').alias('listing_id')).write_parquet(other/"opportunities.parquet")
    write(other/"ready.json", dict(plan_hash=plan["plan_hash"],listing=second,rows=57601,
        files={"opportunities.parquet":file_hash(other/"opportunities.parquet")}))
    args = ["build", "--phase1", str(source),"--min-volume-60s","0","--min-trades-60s","0"]
    assert main(args) == 0
    output = next((tmp_path/"hindsight-greedy"/"2026-08-21").iterdir())
    holding = pl.read_parquet(output/'market_hold_values.parquet')
    opening = pl.read_parquet(output/'market_open_values.parquet')
    assert holding.height == 2*2*57601
    assert holding['time_us'].is_sorted() and opening['time_us'].is_sorted()
    assert opening.height <= holding.height and opening['can_open'].all()
    assert holding.select('listing_index','ticker','side').unique().sort('listing_index','side').to_dicts() == [
        dict(listing_index=0,ticker='B',side='long'),dict(listing_index=0,ticker='B',side='short'),
        dict(listing_index=1,ticker='C',side='long'),dict(listing_index=1,ticker='C',side='short')]
    assert read(output/'complete.json')['tensor']['rows'] == holding.height
    assert read(output/'complete.json')['tensor']['opening']['rows'] == opening.height
    from src.market_engine.hindsight_market_values import MarketValues
    with MarketValues(output) as market:
        one_second = market.at(left+1_000_000)
        assert one_second.height == 4
        assert one_second.filter(pl.col('side') == 'long')['open_value_per_share'].to_list() == pytest.approx(
            [one_second.filter((pl.col('ticker') == 'B') & (pl.col('side') == 'long'))['open_value_per_share'][0]]*2)
        later = market.at(left+4_000_000)
        assert later['hold_value_available'].is_not_null().all()
        assert market.at(left+2_000_000).select('ticker','side').n_unique() == 4
        with pytest.raises(ValueError,match='absent'):
            market.at(left-1_000_000)
    assert main(args) == 0
    assert read(output/"summary.json")["counts"] == dict(completed=0, reused=2, failed=0)
    (output/"STOP").touch()
    assert main(args) == 2 and not (output/"complete.json").exists()
    assert read(output/"summary.json")["status"] == "interrupted"
    (output/"STOP").unlink()
    assert main(args) == 0
    with (folder/"opportunities.parquet").open("ab") as stream:
        stream.write(b"corrupt")
    assert main(args) == 2 and not (output/"complete.json").exists()
    assert read(output/"summary.json")["counts"]["failed"] == 1
