# Session target progression v25

New test candidate, using the shared momentum executor with
`momentum_session_progression=true`. Candidates without this parameter retain
v24 target behavior. All other entry, addition, session, cash, protection,
partial-target liquidation and same-second re-entry rules remain unchanged.

## Targets

The frozen average gap remains anchored to Early Squeeze activation. After
activation, each distinct causally confirmed resistance counts once per ticker
and New York session, including confirmations while flat. Every three new
levels advance 5x -> 8x -> 10x -> 12x -> 13x -> 14x -> ... with no speed limit.
Partial groups and the multiplier persist across exits and checkpoint restore;
a new session resets them. All purchases share the first actual entry fill as their fixed target basis. Additions and later partial fills do not move that basis.

The target counter and each position's stop counter are independent. Stops keep
the three-break, one-resistance-step rule below the lower band, selecting only current causal resistance levels; catalogued levels now marked support are excluded. A new
position resets its stop counter; it does not reset session target progress.

## Qualifying recent re-entry

Current trade price must be strictly above 130% of the first eligible session
trade, and the previous position's first actual entry fill must be no more than
30 seconds old. Additions and unfilled requests do not refresh this clock.
The new position starts at fill price + 2x frozen gap, with a stop one tick below
the lower band of the closest current resistance midpoint below trade price.
Previously accepted resistance identities that became support remain eligible.
If no such level exists or protection is not valid below the executable price,
entry is deferred with its recorded reason, rather than substituting a stop.

On the next session target advancement, this position inherits the resulting
session multiplier. Further targets follow the same session progression. Its
2x start never lowers or resets session progress. Ordinary re-entries start at
the current session multiplier. Existing entry gates still apply.

## Validation scope

Focused regression coverage includes slow triples, duplicate identities,
checkpoint serialization, flat-period progress, new-session reset, actual
per-tranche target amendments and repaired protection at 12x/14x, recent-entry
time boundaries, next-step inheritance, and Strategy/Portfolio/OMS round trips.
No profitability or live-release acceptance is implied.
