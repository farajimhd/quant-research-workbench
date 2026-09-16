//! Boundary-owned encounter state for shared historical/live calculations.
//! No permission, broker state or order authority is inferred here.
use super::{ExitReason, Settings, State, SECOND};
use crate::{
    content_hash,
    event_order::Scope,
    events::Payload,
    market_structure::{
        self,
        scheduler::{Boundary, Kind},
    },
    Error, Result,
};
use serde::{Deserialize, Serialize};
mod checkpoint;
#[cfg(test)]
mod tests;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub tick: f64,
    pub settings: Settings,
    pub maximum_prior_levels: usize,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub boundary_id: String,
    pub sequence: u64,
    pub input_hash: String,
    pub evaluated_at_ns: u64,
    pub blocked: bool,
    pub exit_reason: Option<ExitReason>,
    pub tracked_levels: usize,
}
pub struct Runtime {
    scope: Scope,
    market_hash: String,
    configuration_hash: String,
    config: Config,
    state: State,
    snapshot: Option<Snapshot>,
    failed: bool,
}
impl Runtime {
    /// Apply only to the boundary just observed. Existing external restrictions
    /// remain set. Encounter evidence cannot grant any account permission.
    pub fn restrict(
        &self,
        boundary: &Boundary<'_>,
        admission: &mut crate::candidate_features::AdmissionAuthorities,
        safety: &mut crate::strategy_dispatch::Safety,
    ) -> Result<()> {
        let snapshot = self
            .snapshot()?
            .ok_or_else(|| Error::Unready("encounter boundary not observed".into()))?;
        if snapshot.boundary_id != boundary.id
            || snapshot.sequence != boundary.sequence
            || snapshot.evaluated_at_ns != boundary.evaluated_at_ns
            || snapshot.input_hash != boundary_hash(boundary)?
        {
            return Err(Error::Conflict(
                "encounter restriction boundary differs".into(),
            ));
        }
        admission.encounter_blocked |= snapshot.blocked;
        safety.encounter_exit |= snapshot.exit_reason.is_some();
        Ok(())
    }
    pub fn new(market: &market_structure::Runtime, config: Config) -> Result<Self> {
        market.market()?;
        super::validate(&config.settings, config.tick)?;
        if config.maximum_prior_levels == 0
            || config.maximum_prior_levels > 100_000
            || config.settings.maximum_encounters > 100_000
        {
            return Err(Error::Capacity("encounter stream bounds".into()));
        }
        let scope = market.source_scope();
        let configuration_hash = content_hash(&(
            "arte.encounter-stream.v1",
            &config,
            market.configuration_hash(),
            (scope.provider, scope.instrument, scope.session),
        ))?;
        Ok(Self {
            scope,
            market_hash: market.configuration_hash().into(),
            configuration_hash,
            config,
            state: State::default(),
            snapshot: None,
            failed: false,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("encounter stream requires recovery".into()));
        }
        Ok(())
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn snapshot(&self) -> Result<Option<&Snapshot>> {
        self.available()?;
        Ok(self.snapshot.as_ref())
    }
    pub fn state(&self) -> Result<&State> {
        self.available()?;
        Ok(&self.state)
    }
    /// Retry of an identical pending boundary is a no-op. Errors fail the owner;
    /// callers cannot read possibly partial state or skip the failed boundary.
    pub fn observe(
        &mut self,
        boundary: &Boundary<'_>,
        market: &market_structure::Runtime,
    ) -> Result<bool> {
        self.available()?;
        let result = self.observe_inner(boundary, market);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn observe_inner(
        &mut self,
        boundary: &Boundary<'_>,
        market: &market_structure::Runtime,
    ) -> Result<bool> {
        market.market()?;
        if market.source_scope() != self.scope
            || market.configuration_hash() != self.market_hash
            || boundary.id.len() != 64
            || !boundary
                .id
                .bytes()
                .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
        {
            return Err(Error::Conflict(
                "encounter stream market or boundary identity".into(),
            ));
        }
        let input = boundary.input(String::new());
        if input.event_time_ns > input.evaluated_at_ns
            || input.available_at_ns > input.evaluated_at_ns
            || self
                .snapshot
                .as_ref()
                .is_some_and(|s| s.evaluated_at_ns > input.evaluated_at_ns)
        {
            return Err(Error::Invalid(
                "encounter stream clock rewind or future input".into(),
            ));
        }
        let input_hash = boundary_hash(boundary)?;
        if let Some(old) = &self.snapshot {
            if old.sequence == boundary.sequence {
                if old.input_hash != input_hash {
                    return Err(Error::Conflict("encounter retry boundary changed".into()));
                }
                return Ok(false);
            }
        }
        let expected = self
            .snapshot
            .as_ref()
            .map_or(Some(1), |s| s.sequence.checked_add(1));
        if expected != Some(boundary.sequence) {
            return Err(Error::Conflict(
                "encounter boundary skipped or reordered".into(),
            ));
        }
        let reason = match &boundary.kind {
            Kind::Completed {
                interval_ns: SECOND,
                bar,
                ..
            } => {
                let bars = market.market()?.completed();
                if bars.last().is_none_or(|latest| latest.bar != bar.bar) {
                    return Err(Error::Conflict(
                        "encounter candle differs from market owner".into(),
                    ));
                }
                let previous = bars.len().checked_sub(2).map(|i| &bars[i].bar);
                let (at_ns, levels) = market.prior_strategy_levels()?;
                if at_ns > bar.bar.start_ns
                    || levels.len() > self.config.maximum_prior_levels
                    || levels
                        .iter()
                        .any(|l| l.geometry.confirmed_at_ns > bar.bar.start_ns)
                {
                    return Err(Error::Unready(
                        "encounter prior level view is future or over budget".into(),
                    ));
                }
                let levels: Vec<_> = levels.iter().map(|l| l.geometry.clone()).collect();
                self.state.completed_bar(
                    self.scope.session,
                    &bar.bar,
                    previous,
                    &levels,
                    &self.config.settings,
                    self.config.tick,
                )?
            }
            Kind::Trade {
                observation,
                eligible,
            } => {
                self.require_observation(observation)?;
                if !matches!(observation.payload, Payload::Trade { .. }) {
                    return Err(Error::Invalid("encounter trade boundary payload".into()));
                }
                if *eligible {
                    let Payload::Trade { price, .. } = observation.payload else {
                        return Err(Error::Invalid("encounter trade payload differs".into()));
                    };
                    if price.atoms > (1_i64 << 53) {
                        return Err(Error::Invalid("encounter price precision".into()));
                    }
                    self.state.market_update(
                        self.scope.session,
                        observation.sip.ns,
                        price.to_f64(),
                        true,
                    )?
                } else {
                    None
                }
            }
            Kind::Quote { observation } => {
                self.require_observation(observation)?;
                if !matches!(observation.payload, Payload::Quote { .. }) {
                    return Err(Error::Invalid("encounter quote boundary payload".into()));
                }
                None
            }
            Kind::Completed { .. } => None,
        };
        // Exit reasons are boundary-local. Existing warning/failure state remains.
        self.state.exit_reason = reason;
        self.snapshot = Some(Snapshot {
            boundary_id: boundary.id.into(),
            sequence: boundary.sequence,
            input_hash,
            evaluated_at_ns: boundary.evaluated_at_ns,
            blocked: self.state.blocked(),
            exit_reason: reason,
            tracked_levels: self.state.levels.len(),
        });
        Ok(true)
    }
    fn require_observation(&self, observation: &crate::events::Observation) -> Result<()> {
        observation.validate()?;
        if observation.key.provider != self.scope.provider
            || observation.key.instrument != self.scope.instrument
            || observation.key.session != self.scope.session
        {
            return Err(Error::Conflict(
                "encounter observation scope differs".into(),
            ));
        }
        Ok(())
    }
}
fn boundary_hash(boundary: &Boundary<'_>) -> Result<String> {
    let input = boundary.input(String::new());
    match &boundary.kind {
        Kind::Trade {
            observation,
            eligible,
        } => content_hash(&(&input, "trade", observation, eligible)),
        Kind::Quote { observation } => content_hash(&(&input, "quote", observation)),
        Kind::Completed {
            interval_ns, bar, ..
        } => content_hash(&(&input, "completed", interval_ns, bar)),
    }
}
