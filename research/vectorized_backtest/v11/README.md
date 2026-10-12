# V11: squared session shortfall and relaxed penalties

Copied from V10. Fresh generation-one campaign with the same initialization,
inputs, replay, selection, mutation, lineage and display-only validation.

Cost = -trimmed profit + 0.25*N*trade-tail loss - 0.25*D*session-tail mean
       + 0.10*sum(drawdown) + 0.08*risk-dollar-hours
       + 0.0016*capital-dollar-hours + 3*K
       + concentration + sum(max(0,1000-session_profit)^2/1000).

Concentration retains weight 1: sum(positive session profit squared) divided
by total positive session profit, or zero when no session is profitable.
Squared shortfall weight is 1 and target $1000, both configurable and
checkpoint-bound. There is no block reward or capped profit reward.
Block metrics and linear session shortfall remain descriptive diagnostics;
objective_components.shortfall_penalty contains the actual squared penalty.
An inactive session contributes $1000; a -$500 session $2250; a $500 session
$250; a session at or above $1000 contributes zero shortfall penalty.
Existing trade top-trim 10%, trade tail 10%, session tail 20% remain unchanged.

No broker assumptions, feature computation or GPU replay changes. W&B remains
an independent scalar-only reporter with summary heartbeat. Validation cannot
influence training selection, RNG, parameters or stopping. Data and seed format
identifiers remain compatible with existing V8-certified inputs. V11 outputs
and W&B project optimization-v11 are separate from prior runs.
