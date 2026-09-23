"""Versioned reporting evidence; vendor conditions and low six meta bits survive.

The lag threshold is a research policy, not a determination of FINRA compliance.
An unflagged trade with missing clocks is UNKNOWN, not certified timely.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REVISION = "trade_reporting_v1_conditions_5_13_30_31_32_33_prior_date_lag_gt10s"
EVALUATED = 0x40
DELAYED = 0x80
LOW_MASK = 0x3F
LAG_NS = 10_000_000_000
DELAYED_CONDITIONS = (5, 13, 30, 31, 32, 33)
NY = ZoneInfo("America/New_York")


def reporting_reason(conditions, participant, sip):
    """Reason mask: 1 explicit, 2 prior NY date, 4 >10s lag, 8 unknown clock."""
    codes = set()
    for item in str(conditions or "").split(","):
        try:
            codes.add(int(item.strip()))
        except ValueError:
            pass
    explicit = bool(codes.intersection(DELAYED_CONDITIONS))
    try:
        p, s = int(participant), int(sip)
        valid = 0 < p <= s <= 0x7FFFFFFFFFFFFFFF
    except (TypeError, ValueError):
        p = s = 0
        valid = False
    if not valid:
        return int(explicit) | 8
    prior = datetime.fromtimestamp(p // 1_000_000_000, timezone.utc).astimezone(NY).date() < datetime.fromtimestamp(s // 1_000_000_000, timezone.utc).astimezone(NY).date()
    return int(explicit) | (2 if prior else 0) | (4 if s - p > LAG_NS else 0)


def flags_from_reason(reason):
    return EVALUATED | DELAYED if reason & 7 else (0 if reason & 8 else EVALUATED)


def reporting_flags(conditions, participant, sip):
    return flags_from_reason(reporting_reason(conditions, participant, sip))


def reason_sql(conditions="conditions", participant="participant_timestamp", sip="sip_timestamp"):
    p, s = f"toUInt64OrZero({participant})", f"toUInt64OrZero({sip})"
    valid = f"({p}>0 AND {p}<={s} AND {s}<=9223372036854775807)"
    codes = ",".join(map(str, DELAYED_CONDITIONS))
    explicit = f"hasAny(arrayMap(x -> toInt32OrZero(trimBoth(x)), splitByChar(',', {conditions})), [{codes}])"
    # Signed subtraction avoids unsigned underflow even if ClickHouse evaluates all branches.
    lag = f"(toInt128({s})-toInt128({p})>{LAG_NS})"
    prior = f"(toDate(fromUnixTimestamp64Nano(toInt64(least({p},9223372036854775807))), 'America/New_York') < toDate(fromUnixTimestamp64Nano(toInt64(least({s},9223372036854775807))), 'America/New_York'))"
    return f"toUInt8(toUInt8({explicit}) + if({valid}, 2*toUInt8({prior})+4*toUInt8({lag}), 8))"


def flags_sql(reason="reporting_reason"):
    return f"toUInt8(multiIf(bitAnd({reason},7)!=0,192,bitAnd({reason},8)!=0,0,64))"
