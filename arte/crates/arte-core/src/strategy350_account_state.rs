//! Account-owned Strategy 350 state shared by modeled playback and live mode.
//! The transaction journal, not this value, owns commit and rollback.
use crate::{
    event_order::Scope as MarketScope,
    strategy350_effective::Config,
    strategy350_gap::FrozenGap,
    strategy350_reentry::State as ReentryState,
    strategy350_targets::{BreakEvent, Progress, Upgrade},
    Result,
};
use serde::{Deserialize, Serialize};

const MAX_TARGET_IMAGE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    targets: Progress,
    reentry: ReentryState,
}
impl State {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        effective: &Config,
        session_wide_targets: bool,
        market_scope: MarketScope,
        account: String,
        run_id: String,
        session_start_ns: u64,
        session_end_ns: u64,
    ) -> Result<Self> {
        effective.validate()?;
        Ok(Self {
            targets: Progress::new(&effective.target_progress, session_wide_targets)?,
            reentry: ReentryState::new(
                &effective.reentry,
                market_scope,
                account,
                run_id,
                session_start_ns,
                session_end_ns,
            )?,
        })
    }
    pub fn validate(&self, effective: &Config) -> Result<()> {
        self.validate_quick(effective)?;
        self.targets
            .checkpoint(&effective.target_progress, MAX_TARGET_IMAGE_BYTES)?;
        self.reentry.validate(&effective.reentry)?;
        Ok(())
    }
    /// Constant-time guard for the decision hot path. Full semantic validation
    /// runs at construction, checkpoint and recovery, never per market event.
    pub fn validate_quick(&self, effective: &Config) -> Result<()> {
        effective.require_target_progress(self.targets.configuration_hash())?;
        effective.require_reentry(self.reentry.configuration_hash())?;
        if self.targets.distinct_count() > effective.target_progress.maximum_distinct_levels {
            return Err(crate::Error::Capacity(
                "Strategy 350 account target budget".into(),
            ));
        }
        Ok(())
    }
    pub fn require_owner(&self, scope: &crate::strategy_dispatch::Scope) -> Result<()> {
        if scope.strategy_kind != crate::strategy_dispatch::StrategyKind::Strategy350
            || self.reentry.account() != scope.account
            || self.reentry.run_id() != scope.run_id
            || self.reentry.scope().instrument != scope.instrument
        {
            return Err(crate::Error::Conflict(
                "Strategy 350 account state owner".into(),
            ));
        }
        Ok(())
    }
    pub fn require_market_scope(&self, scope: MarketScope) -> Result<()> {
        if self.reentry.scope() != scope {
            return Err(crate::Error::Conflict(
                "Strategy 350 account market session differs".into(),
            ));
        }
        Ok(())
    }
    pub fn targets(&self) -> &Progress {
        &self.targets
    }
    pub fn reentry(&self) -> &ReentryState {
        &self.reentry
    }
    pub fn require_projected_position(
        &self,
        position: Option<&crate::execution_positions::Position>,
    ) -> Result<()> {
        self.reentry.require_projected_position(position)
    }
    pub fn reentry_mut(&mut self) -> &mut ReentryState {
        &mut self.reentry
    }
    pub fn observe_breaks(
        &mut self,
        effective: &Config,
        breaks: &[BreakEvent],
        known_at_ns: u64,
    ) -> Result<Vec<Upgrade>> {
        self.validate_quick(effective)?;
        self.targets
            .observe(breaks, known_at_ns, &effective.target_progress)
    }
    pub fn target_price(
        &self,
        effective: &Config,
        gap: &FrozenGap,
        entry_basis: f64,
        tick: f64,
    ) -> Result<f64> {
        self.validate_quick(effective)?;
        effective.require_gap(gap)?;
        gap.target_price(entry_basis, self.targets.multiplier(), tick)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution_interval::ExecutionInterval;
    #[test]
    fn target_state_is_bound_to_the_effective_account_configuration() {
        let effective = crate::strategy350_effective::test_config(ExecutionInterval::Events);
        let mut state = State::new(
            &effective,
            true,
            MarketScope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            "first".into(),
            "run".into(),
            1_000_000_000,
            100_000_000_000,
        )
        .unwrap();
        state.validate(&effective).unwrap();
        let breaks = [
            BreakEvent {
                level_id: "a".into(),
                opened_at_ns: 1_000_000_000,
            },
            BreakEvent {
                level_id: "b".into(),
                opened_at_ns: 2_000_000_000,
            },
            BreakEvent {
                level_id: "c".into(),
                opened_at_ns: 3_000_000_000,
            },
        ];
        assert_eq!(
            state
                .observe_breaks(&effective, &breaks, 3_000_000_000)
                .unwrap()[0]
                .multiplier,
            8
        );
        let mut changed = effective.clone();
        changed.target_progress.maximum_distinct_levels += 1;
        assert!(state.validate(&changed).is_err());
        assert!(state.observe_breaks(&changed, &[], 3_000_000_000).is_err());
        let restored: State = serde_json::from_slice(&serde_json::to_vec(&state).unwrap()).unwrap();
        restored.validate(&effective).unwrap();
        let owner = crate::strategy_dispatch::Scope {
            run_id: "run".into(),
            mode: crate::strategy_dispatch::Mode::Backtest,
            account: "first".into(),
            strategy_instance: "strategy-350".into(),
            strategy_kind: crate::strategy_dispatch::StrategyKind::Strategy350,
            execution_interval: ExecutionInterval::Events,
            instrument: 10,
            code_hash: "a".repeat(64),
            config_hash: effective.hash().unwrap(),
        };
        restored.require_owner(&owner).unwrap();
        restored
            .require_market_scope(MarketScope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            })
            .unwrap();
        assert!(restored
            .require_market_scope(MarketScope {
                session: 20260923,
                ..restored.reentry().scope()
            })
            .is_err());
    }
}
