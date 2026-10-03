"""Label first expansions and evaluate first-pass filters without hindsight entry."""
from dataclasses import asdict, dataclass
import numpy as np
import polars as pl

FEATURES = ("volume_30s", "volume_60s", "volume_300s", "trades_30s",
            "trades_60s", "trades_300s", "dollars_30s", "dollars_300s",
            "volume_acceleration", "trade_acceleration", "prior_return_60s",
            "session_volume", "rvol", "float_shares", "float_turnover",
            "baseline_range", "seconds_since_signal")


@dataclass(frozen=True)
class Settings:
    baseline_bars: int = 20
    minimum_history: int = 5
    range_multiplier: float = 2.0
    minimum_range: float = .005
    upward_close_location: float = .75
    prediction_seconds: int = 30
    prior_sessions: int = 13
    minimum_rvol_sessions: int = 5
    minimum_passes: int = 10

    def validate(self):
        integers = (self.baseline_bars,self.minimum_history,self.prediction_seconds,
                    self.prior_sessions,self.minimum_rvol_sessions,self.minimum_passes)
        if any(type(v) is not int or v < 1 for v in integers):
            raise ValueError("Counts and durations must be positive integers")
        if not self.minimum_history <= self.baseline_bars <= 120:
            raise ValueError("Range history must be bounded and have enough samples")
        if not self.minimum_rvol_sessions <= self.prior_sessions <= 60:
            raise ValueError("RVOL requires a bounded number of earlier sessions")
        if (not np.isfinite(self.range_multiplier) or self.range_multiplier <= 1
                or not 0 < self.minimum_range < 1 or not .5 < self.upward_close_location <= 1
                or self.prediction_seconds % 30 or self.prediction_seconds > 1800):
            raise ValueError("Invalid expansion or prediction contract")
        return self


def dense_bars(bars, tickers, start_us, end_us):
    """Absent buckets in a certified sparse source have zero activity, no prices."""
    if (end_us <= start_us or (end_us-start_us) % 30_000_000
            or len(set(tickers)) != len(tickers) or not tickers):
        raise ValueError("Require unique tickers and aligned thirty-second bounds")
    if bars.select(pl.struct("ticker","time_us").n_unique()).item() != bars.height:
        raise ValueError("Duplicate thirty-second candle key")
    if bars.filter(~pl.col("ticker").is_in(tickers)
                   | (pl.col("time_us") <= start_us) | (pl.col("time_us") > end_us)
                   | ((pl.col("time_us")-start_us) % 30_000_000 != 0)).height:
        raise ValueError("Candle key outside certified session grid")
    if bars.filter((pl.col("volume") < 0) | (pl.col("trades") < 0)
                   | (pl.col("notional") < 0) | ~pl.all_horizontal(
                       *[pl.col(k).is_not_null() & pl.col(k).is_finite() for k in ("volume","trades","notional")])).height:
        raise ValueError("Invalid published activity")
    clocks=pl.DataFrame({"time_us":np.arange(start_us+30_000_000,end_us+1,30_000_000)})
    grid=pl.DataFrame({"ticker":tickers}).join(clocks,how="cross")
    result=grid.join(bars.with_columns(pl.lit(True).alias("published")),
                     on=["ticker","time_us"],how="left",validate="1:1")
    return result.with_columns(
        pl.col("published").fill_null(False),
        pl.col("valid").fill_null(False),
        *[pl.col(k).fill_null(0) for k in ("volume","trades","notional")],
        ((pl.col("time_us")-start_us)//1_000_000).alias("offset_seconds")
    ).sort("ticker","time_us")


def build_observations(bars, signals, *, day, start_us, end_us, settings=Settings(),
                       context=None, rvol=None):
    settings.validate()
    if signals["ticker"].n_unique()!=signals.height or signals.height==0:
        raise ValueError("Require one first signal per ticker/session")
    frame=dense_bars(bars,signals["ticker"].to_list(),start_us,end_us)
    bad=frame.filter(pl.col("valid") & (
        (pl.col("open")<=0) | (pl.col("low")<=0)
        | (pl.col("high")<pl.max_horizontal("open","close","low"))
        | (pl.col("low")>pl.min_horizontal("open","close"))
        | ~pl.all_horizontal(*[pl.col(k).is_finite() for k in ("open","close","high","low")])
    ))
    if bad.height:raise ValueError("Invalid published OHLC geometry")
    frame=frame.with_columns(
        pl.when(pl.col("valid")).then((pl.col("high")-pl.col("low"))/pl.col("open")).alias("range_fraction"),
        (pl.col("time_us")-30_000_000).alias("decision_us"),
        (pl.col("offset_seconds")-30).alias("decision_offset"))
    frame=frame.with_columns(
        pl.col("range_fraction").shift(1).rolling_median(settings.baseline_bars,
            min_samples=settings.minimum_history).over("ticker").alias("baseline_range"),
        pl.col("volume").cum_sum().shift(1).fill_null(0).over("ticker").alias("session_volume"),
        pl.col("close").shift(1).over("ticker").alias("prior_close"),
        (pl.col("close").shift(1)/pl.col("close").shift(3)-1).over("ticker").alias("prior_return_60s"),
        *[pl.col(source).shift(1).rolling_sum(n,min_samples=n).over("ticker").alias(f"{name}_{n*30}s")
          for source,name in (("volume","volume"),("trades","trades"),("notional","dollars"))
          for n in (1,2,10)])
    # Acceleration compares the last completed bar to ten preceding bars,
    # excluding that last bar from its denominator.
    frame=frame.with_columns(
        *[(pl.col(source).shift(2).rolling_mean(10,min_samples=10).over("ticker")).alias(f"{name}_reference")
          for source,name in (("volume","volume"),("trades","trade"))])
    frame=frame.with_columns(
        pl.when(pl.col("volume_reference")>0).then(pl.col("volume_30s")/pl.col("volume_reference")).alias("volume_acceleration"),
        pl.when(pl.col("trade_reference")>0).then(pl.col("trades_30s")/pl.col("trade_reference")).alias("trade_acceleration"),
        pl.when(pl.col("valid") & pl.col("baseline_range").is_not_null()).then(
            pl.col("range_fraction")>=pl.max_horizontal(pl.lit(settings.minimum_range),
                pl.col("baseline_range")*settings.range_multiplier)).fill_null(False).alias("expansion"),
        pl.when(pl.col("high")>pl.col("low")).then(
            (pl.col("close")-pl.col("low"))/(pl.col("high")-pl.col("low"))).alias("close_location"))
    frame=frame.with_columns(
        pl.when(pl.col("expansion") & (pl.col("close")>pl.col("open"))
                & (pl.col("close_location")>=settings.upward_close_location)).then(pl.lit("up"))
        .when(pl.col("expansion") & (pl.col("close")<pl.col("open"))
                & (pl.col("close_location")<=1-settings.upward_close_location)).then(pl.lit("down"))
        .when(pl.col("expansion")).then(pl.lit("two_sided")).otherwise(pl.lit(None)).alias("event_kind"))
    session_stats=frame.filter(pl.col("valid")).group_by("ticker").agg(
        pl.col("open").first().alias("session_first_observed_open"),
        pl.col("close").last().alias("session_last_observed_close"),
        pl.col("range_fraction").max().alias("session_max_range"))
    frame=frame.join(signals,on="ticker",validate="m:1").filter(pl.col("decision_us")>=pl.col("signal_us"))
    events=frame.filter(pl.col("expansion")).group_by("ticker").agg(
        pl.col("decision_us").first().alias("event_us"),pl.col("event_kind").first().alias("first_event_kind"),
        pl.col("range_fraction").first().alias("event_range"),pl.col("baseline_range").first().alias("event_baseline"),
        (pl.col("close")/pl.col("open")-1).first().alias("event_return"))
    frame=frame.join(events,on="ticker",how="left",validate="m:1")
    risk=frame.filter(pl.col("event_us").is_null() | (pl.col("decision_us")<=pl.col("event_us")))
    # Never call early, unobservable range history a quiet negative.
    unknown=risk.group_by("ticker").agg(
        (pl.col("valid") & pl.col("baseline_range").is_null()).sum().alias("unlabelable_candles"))
    last=frame.filter(pl.col("valid")).group_by("ticker").agg(
        pl.col("close").last().alias("last_observed_close"),pl.col("time_us").last().alias("last_price_us"),
        pl.col("open").first().alias("first_post_signal_open"),
        pl.col("high").max().alias("post_signal_high"),pl.col("low").min().alias("post_signal_low"))
    cases=signals.join(events,on="ticker",how="left",validate="1:1").join(unknown,on="ticker",how="left").join(last,on="ticker",how="left")
    cases=cases.join(session_stats,on="ticker",how="left",validate="1:1")
    cases=cases.with_columns(pl.lit(day).alias("session"),
        pl.when(pl.col("unlabelable_candles").fill_null(0)>0).then(pl.lit("unknown_history"))
        .when(pl.col("last_price_us").is_null()).then(pl.lit("no_post_signal_prices"))
        .otherwise(pl.col("first_event_kind").fill_null("quiet")).alias("outcome"),
        ((pl.col("event_us")-pl.col("signal_us"))/1_000_000).alias("signal_to_event_seconds"),
        (pl.col("session_last_observed_close")/pl.col("session_first_observed_open")-1).alias("session_observed_return"),
        (pl.col("last_observed_close")/pl.col("first_post_signal_open")-1).alias("post_signal_observed_return"),
        (pl.col("post_signal_high")/pl.col("first_post_signal_open")-1).alias("post_signal_max_up"),
        (pl.col("post_signal_low")/pl.col("first_post_signal_open")-1).alias("post_signal_max_down"),
        (pl.col("event_range")/pl.col("event_baseline")).alias("event_range_multiple"))
    risk=risk.join(cases.select("ticker","outcome"),on="ticker",validate="m:1")
    if context is None:context=pl.DataFrame({"ticker":signals["ticker"],"float_shares":[None]*signals.height},schema_overrides={"float_shares":pl.Float64})
    risk=risk.join(context.select("ticker","float_shares"),on="ticker",how="left",validate="m:1")
    if rvol is None:
        risk=risk.with_columns(pl.lit(None,dtype=pl.Float64).alias("rvol_expected_volume"),
                               pl.lit(0).alias("rvol_sessions"))
    else:risk=risk.join(rvol,on=["ticker","decision_offset"],how="left",validate="m:1")
    risk=risk.with_columns(pl.lit(day).alias("session"),
        ((pl.col("decision_us")-pl.col("signal_us"))/1_000_000).alias("seconds_since_signal"),
        pl.when(pl.col("float_shares")>0).then(pl.col("session_volume")/pl.col("float_shares")).alias("float_turnover"),
        pl.when((pl.col("rvol_expected_volume")>0) & (pl.col("rvol_sessions")>=settings.minimum_rvol_sessions))
        .then(pl.col("session_volume")/pl.col("rvol_expected_volume")).alias("rvol"))
    eligible=risk.filter(~pl.col("outcome").is_in(["unknown_history","no_post_signal_prices"])
                         & pl.col("baseline_range").is_not_null())
    return eligible.select("session","ticker","signal_us","decision_us","event_us","outcome",*FEATURES),cases


def evaluate(observations,cases,rule,prediction_seconds=30):
    expression=pl.lit(True)
    for feature,operator,value in rule:
        column=pl.col(feature)
        expression &= column.is_not_null() & column.is_finite() & (column>=value if operator==">=" else column<=value)
    first=observations.filter(expression).sort("session","ticker","decision_us").group_by("session","ticker").agg(
        pl.col("decision_us").first().alias("entry_us"))
    known=cases.filter(~pl.col("outcome").is_in(["unknown_history","no_post_signal_prices"]))
    joined=known.join(first,on=["session","ticker"],how="left",validate="1:1")
    positive=joined["outcome"].eq("up").sum()
    selected=joined["entry_us"].is_not_null()
    success=(selected & joined["outcome"].eq("up") &
             ((joined["event_us"]-joined["entry_us"])>=0) &
             ((joined["event_us"]-joined["entry_us"])<prediction_seconds*1_000_000)).fill_null(False)
    tp=int(success.sum());count=int(selected.sum());negatives=joined.height-int(positive)
    false_nonmovers=int((selected & ~joined["outcome"].eq("up")).sum())
    precision=tp/count if count else 0.;recall=tp/positive if positive else 0.
    return dict(cases=joined.height,upward_events=int(positive),selected=count,true_positives=tp,
        precision=precision,recall=recall,f1=2*precision*recall/(precision+recall) if precision+recall else 0.,
        nonmover_rejection=1-false_nonmovers/negatives if negatives else None,
        early_selections=count-tp-false_nonmovers)


def discover(observations,cases,settings=Settings()):
    """Choose rules on earlier dates, evaluate frozen rules on the final date."""
    days=sorted(cases["session"].unique().to_list())
    if len(days)<2:raise ValueError("At least two sessions required for temporal evaluation")
    train=observations.filter(pl.col("session").is_in(days[:-1]));train_cases=cases.filter(pl.col("session").is_in(days[:-1]))
    test=observations.filter(pl.col("session")==days[-1]);test_cases=cases.filter(pl.col("session")==days[-1])
    rows=[];seen=set()
    def add(rule):
        identity=tuple(sorted(tuple(term) for term in rule))
        if identity in seen:return
        seen.add(identity);metrics=evaluate(train,train_cases,rule,settings.prediction_seconds)
        rows.append(dict(rule=[list(x) for x in rule],training=metrics))
    add([])
    for feature in FEATURES:
        values=train[feature].drop_nulls().filter(train[feature].drop_nulls().is_finite())
        if len(values)<settings.minimum_passes:continue
        for quantile in (.1,.25,.4,.5,.6,.75,.9):
            threshold=float(values.quantile(quantile))
            for operator in (">=","<="):add([(feature,operator,threshold)])
    ranked=sorted((r for r in rows if r["rule"] and r["training"]["selected"]>=settings.minimum_passes),
        key=lambda r:(-r["training"]["f1"],-r["training"]["nonmover_rejection"] if r["training"]["nonmover_rejection"] is not None else 0,str(r["rule"])))
    top=ranked[:8]
    for i,left in enumerate(top):
        for right in top[i+1:]:
            if left["rule"][0][0]!=right["rule"][0][0]:add(left["rule"]+right["rule"])
    rows.sort(key=lambda r:(-(r["training"]["f1"] if r["training"]["selected"]>=settings.minimum_passes else -1),len(r["rule"]),str(r["rule"])))
    for rank,row in enumerate(rows,1):
        row.update(rank=rank,validation=evaluate(test,test_cases,row["rule"],settings.prediction_seconds))
    baseline=next(r for r in rows if not r["rule"])
    supported=[r for r in rows if r["rule"] and r["training"]["selected"]>=settings.minimum_passes
               and r["training"]["true_positives"]>=3 and r["training"]["f1"]>baseline["training"]["f1"]]
    return dict(settings=asdict(settings),training_sessions=days[:-1],validation_session=days[-1],
        selection="training F1 only; validation never selects or tunes thresholds",rules=rows,
        recommended_rule=supported[0] if supported else None,
        recommendation_status="exploratory_training_candidate" if supported else "no_supported_improvement")
