# Explicit state rebuild origin

[Unified launcher](13j-launcher-execution.md) · [Checkpoint seeds](13a-checkpoint-seeds.md)

Fresh `launch --rebuild-origin-date YYYY-MM-DD` permits a versioned empty
indicator/V7 state only for a family without a compatible causal predecessor.
Omitting it retains the missing-state blocker. The date must be an exchange
session within the explicit source context and no later than the first target session.
Every intervening exchange session must have certified canonical/reporting
inputs and full-session dated membership; a missing session blocks planning.
Existing compatible states remain preferred independently for each family.

`technical_origin` and `v7_origin` roots depend on the certified origin source
and universe. Their bounded bindings record version, listing, ticker, date,
recipe, trade policy, source hash, causal origin hash and certificate hash in
the existing immutable plan. No new table or duplicated checkpoint is needed.
The certificate binds the whole origin source day; the causal origin hash
excludes its full-day fingerprint so later-day corrections cannot alter the
empty PM starting state. Generated PM/regular/AH states use the existing
lossless full-state codec and `live_market_ssd` tables. Resume verifies the
frozen roots and persisted state instead of initializing again.

This is a declared rebuild boundary, not proof that the security had no earlier
history. A later origin produces a separately bound lineage; it must not be
described as equivalent to replay from an earlier origin. Listings without
membership at the selected origin remain blocked rather than moving the date
silently. Legacy retrospective labels still do not qualify as causal seeds.

A bounded real ClickHouse fixture runs two chronological days from explicit
origins, persists all six session boundaries, carries selected 1-second/minute
indicator state into day two, and resumes the identical seal with zero inserts.
It checks one shared event aggregate per day. Synthetic event inputs and source
roots are confined to its isolated test database; this is execution correctness
evidence, not certification of production source coverage or throughput.
