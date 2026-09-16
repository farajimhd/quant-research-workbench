//! Shared boundary-aligned market features; never account admission or order authority.
use crate::{
    content_hash,
    market_structure::{
        scheduler::{Boundary, Kind},
        Runtime,
    },
    strategy_macd::{Reading, State as Macd},
    strategy_setup::{ActivityEvidence, ActivityState, Range, SetupSettings, SetupState},
    Error, Result,
};
use serde::{Deserialize, Serialize};
pub mod checkpoint;
const SECOND: u64 = 1_000_000_000;
#[derive(Clone, Serialize, Deserialize)]
pub struct Config {
    pub setup: SetupSettings,
    pub forming_macd: bool,
    pub minimum_range_pct: f64,
    pub minimum_progress_pct: f64,
    pub maximum_quote_age_ns: u64,
    pub maximum_completed_bar_age_ns: u64,
    pub maximum_levels: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OneSecond {
    pub at_ns: u64,
    pub previous_bar_end_ns: Option<u64>,
    pub range: Option<Range>,
    pub range_activity: ActivityEvidence,
    pub progress_activity: ActivityEvidence,
    pub vwap: f64,
    pub high: f64,
    pub prior_high: Option<f64>,
    pub levels: Vec<crate::strategy_targets::TargetLevel>,
}
impl OneSecond {
    pub fn activity_block(&self) -> Option<&str> {
        [&self.range_activity, &self.progress_activity]
            .into_iter()
            .find(|evidence| evidence.minimum_pct > 0. && !evidence.passed)
            .map(|evidence| evidence.reason.as_str())
    }
}
/// Explicit non-market authorities. They are not inferred from feature readiness.
#[derive(Clone, Copy)]
pub struct EntryContext<'a> {
    pub admission: &'a crate::strategy_entry::Admission,
    pub swings: &'a [crate::strategy_targets::Swing],
    pub regular: bool,
    pub regular_target: Option<f64>,
    pub recovery: &'a crate::strategy_lifecycle::RecoveryState,
    pub recovery_policy: &'a crate::strategy_lifecycle::RecoveryPolicy,
}
/// Admission/account authorities not inferred from price or indicator state.
pub struct AcquisitionContext {
    pub tradable: bool,
    pub regular_block: bool,
    pub encounter_blocked: bool,
    pub pending_capital: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub boundary_id: String,
    pub sequence: u64,
    pub available_at_ns: u64,
    pub evaluated_at_ns: u64,
    pub macd: Option<Reading>,
    pub one_second: Option<OneSecond>,
}
pub struct State {
    config: Config,
    config_hash: String,
    market_hash: String,
    scope: crate::event_order::Scope,
    macd: Macd,
    setup: SetupState,
    activity: ActivityState,
    snapshot: Option<Snapshot>,
    failed: bool,
}
impl State {
    pub fn new(market: &Runtime, config: Config) -> Result<Self> {
        market.market()?;
        if !market.timeframe(5 * SECOND)?.macd_periods_match(12, 26, 9)
            || config.setup.range_ns == 0
            || config.setup.range_ns > 3600 * SECOND
            || config.setup.minimum_bars == 0
            || config.setup.minimum_bars > 3600
            || config.setup.maximum_gap_ns > config.setup.range_ns
            || config.maximum_quote_age_ns == 0
            || config.maximum_quote_age_ns > 60 * SECOND
            || config.maximum_completed_bar_age_ns == 0
            || config.maximum_completed_bar_age_ns > 60 * SECOND
            || config.maximum_levels == 0
            || config.maximum_levels > 100_000
            || [config.minimum_range_pct, config.minimum_progress_pct]
                .iter()
                .any(|value| !value.is_finite() || *value < 0.)
        {
            return Err(Error::Invalid(
                "candidate feature configuration or timeframe".into(),
            ));
        }
        Ok(Self {
            config_hash: content_hash(&(
                "candidate-market-features-v2",
                market.configuration_hash(),
                &config,
            ))?,
            market_hash: market.configuration_hash().into(),
            scope: market.source_scope(),
            macd: Macd::new(config.forming_macd),
            config,
            setup: SetupState::new(),
            activity: ActivityState::default(),
            snapshot: None,
            failed: false,
        })
    }
    pub fn configuration_hash(&self) -> &str {
        &self.config_hash
    }
    pub(crate) fn config_identity(&self) -> Result<String> {
        content_hash(&self.config)
    }
    pub fn maximum_completed_bar_age_ns(&self) -> u64 {
        self.config.maximum_completed_bar_age_ns
    }
    pub fn source_scope(&self) -> crate::event_order::Scope {
        self.scope
    }
    pub fn snapshot(&self) -> Result<Option<&Snapshot>> {
        if self.failed {
            return Err(Error::Unready("candidate features require recovery".into()));
        }
        Ok(self.snapshot.as_ref())
    }
    /// Derive intrabar market operands from exactly the pending shared boundary.
    pub fn acquisition_frame(
        &self,
        boundary: &Boundary<'_>,
        market: &Runtime,
        quotes: &crate::quote_state::Book,
        context: AcquisitionContext,
    ) -> Result<(crate::strategy_candidate::AcquisitionObservation, f64)> {
        let snapshot = self
            .snapshot()?
            .ok_or_else(|| Error::Unready("candidate features missing".into()))?;
        if snapshot.boundary_id != boundary.id
            || snapshot.sequence != boundary.sequence
            || snapshot.evaluated_at_ns != boundary.evaluated_at_ns
            || market.configuration_hash() != self.market_hash
        {
            return Err(Error::Conflict("intrabar feature boundary differs".into()));
        }
        let Kind::Trade {
            observation,
            eligible: true,
        } = &boundary.kind
        else {
            return Err(Error::Unready(
                "intrabar acquisition requires an eligible trade".into(),
            ));
        };
        let crate::events::Payload::Trade { price, .. } = observation.payload else {
            return Err(Error::Invalid("intrabar trade payload differs".into()));
        };
        if price.atoms > (1_i64 << 53) {
            return Err(Error::Invalid(
                "intrabar trade exceeds exact integer conversion range".into(),
            ));
        }
        let series = market.market()?;
        let bar = series
            .developing()
            .ok_or_else(|| Error::Unready("intrabar candle missing".into()))?;
        if observation.sip.ns < bar.start_ns || observation.sip.ns >= bar.end_ns {
            return Err(Error::Conflict(
                "intrabar candle does not contain pending trade".into(),
            ));
        }
        let macd = snapshot.macd.as_ref();
        let frame = crate::strategy_candidate::AcquisitionObservation {
            quote_policy_hash: String::new(),
            at_ns: boundary.evaluated_at_ns,
            price: price.to_f64(),
            ask: 0.,
            vwap: series.session_vwap()?,
            macd_at_ns: macd.map(|reading| reading.at_ns),
            macd_positive: macd.is_some_and(|reading| reading.positive()),
            macd_episode_present: macd.is_some_and(|reading| reading.episode_at_ns.is_some()),
            tradable: context.tradable,
            regular_block: context.regular_block,
            encounter_blocked: context.encounter_blocked,
            pending_capital: context.pending_capital,
        }
        .bind_quote(quotes, self.scope, self.config.maximum_quote_age_ns)?;
        Ok((frame, bar.open.max(bar.close)))
    }
    /// Borrow shared arrays once for each account's candidate evaluation. This
    /// only constructs an entry frame; it neither grants admission nor processes
    /// emergency exits, which must remain independent of entry-data readiness.
    pub fn entry_frame<'a>(
        &'a self,
        boundary: &Boundary<'_>,
        evaluated_at_ns: u64,
        market: &'a Runtime,
        quotes: &'a crate::quote_state::Book,
        context: EntryContext<'a>,
    ) -> Result<crate::strategy_entry::Frame<'a>> {
        let snapshot = self
            .snapshot()?
            .ok_or_else(|| Error::Unready("candidate features missing".into()))?;
        let one = snapshot.one_second.as_ref().ok_or_else(|| {
            Error::Unready("entry requires a completed one-second boundary".into())
        })?;
        if snapshot.boundary_id != boundary.id
            || snapshot.sequence != boundary.sequence
            || snapshot.evaluated_at_ns != boundary.evaluated_at_ns
            || evaluated_at_ns < snapshot.evaluated_at_ns
            || market.configuration_hash() != self.market_hash
        {
            return Err(Error::Conflict(
                "entry frame boundary or market differs".into(),
            ));
        }
        let bars = market.market()?.completed();
        let bar = &bars
            .last()
            .ok_or_else(|| Error::Unready("entry bar missing".into()))?
            .bar;
        if bar.end_ns != one.at_ns
            || context.admission.at_ns > bar.end_ns
            || context
                .admission
                .detector_at_ns
                .is_some_and(|at| at > bar.end_ns)
            || context.admission.macd_at_ns != snapshot.macd.as_ref().map(|reading| reading.at_ns)
            || context.admission.macd_positive
                != snapshot
                    .macd
                    .as_ref()
                    .is_some_and(|reading| reading.positive())
            || context.admission.activity_block.as_deref() != one.activity_block()
            || context
                .regular_target
                .is_some_and(|price| !price.is_finite() || price <= 0.)
        {
            return Err(Error::Conflict(
                "entry admission differs from causal feature evidence".into(),
            ));
        }
        if context.swings.len() > self.config.maximum_levels {
            return Err(Error::Capacity("entry swing evidence budget".into()));
        }
        for swing in context.swings {
            if swing.id.is_empty()
                || swing.pivot_at_ns == 0
                || swing.pivot_at_ns > swing.confirmed_at_ns
                || swing.confirmed_at_ns > bar.end_ns
                || [swing.lower, swing.price, swing.upper]
                    .iter()
                    .any(|price| !price.is_finite() || *price <= 0.)
                || swing.lower > swing.price
                || swing.price > swing.upper
            {
                return Err(Error::Invalid(
                    "entry swing geometry or causal clock".into(),
                ));
            }
        }
        let quote = quotes.require_executable(evaluated_at_ns, self.config.maximum_quote_age_ns)?;
        let scope = market.source_scope();
        if quote.key.provider != scope.provider
            || quote.key.instrument != scope.instrument
            || quote.key.session != scope.session
        {
            return Err(Error::Conflict("entry quote scope differs".into()));
        }
        let crate::events::Payload::Quote { bid, ask, .. } = &quote.payload else {
            unreachable!()
        };
        if bid.atoms > (1_i64 << 53) || ask.atoms > (1_i64 << 53) {
            return Err(Error::Invalid(
                "entry quote exceeds exact integer conversion range".into(),
            ));
        }
        let (prior_at, prior_levels) = market.prior_strategy_levels()?;
        if prior_levels.len() > self.config.maximum_levels {
            return Err(Error::Capacity("prior entry level evidence budget".into()));
        }
        if prior_at > bar.start_ns {
            return Err(Error::Conflict("future prior entry levels".into()));
        }
        Ok(crate::strategy_entry::Frame {
            quote_policy_hash: quotes.policy_hash()?,
            bar,
            previous: bars
                .len()
                .checked_sub(2)
                .map(|index| &bars[index].bar)
                .filter(|previous| Some(previous.end_ns) == one.previous_bar_end_ns),
            bid: bid.to_f64(),
            ask: ask.to_f64(),
            fresh: evaluated_at_ns
                .checked_sub(bar.end_ns)
                .is_some_and(|age| age < self.config.maximum_completed_bar_age_ns),
            hod: Some(one.high),
            prior_hod: one.prior_high,
            vwap: Some(one.vwap),
            range: one.range.as_ref(),
            prior_episode_high: snapshot
                .macd
                .as_ref()
                .and_then(|reading| reading.prior_episode_high),
            prior_levels,
            levels: &one.levels,
            swings: context.swings,
            regular_target: context.regular_target,
            regular: context.regular,
            admission: context.admission,
            recovery: context.recovery,
            recovery_policy: context.recovery_policy,
        })
    }
    /// Consume every scheduler boundary in sequence, before acknowledging it.
    /// Repeating the same pending boundary does not update rolling calculations.
    /// Sequential state mutations are hidden if any component fails; no full
    /// rolling-history clone occurs per event. Coherent recovery is still required.
    pub fn observe(&mut self, boundary: &Boundary<'_>, market: &Runtime) -> Result<bool> {
        self.snapshot()?;
        market.market()?;
        if boundary.id.len() != 64 || !boundary.id.bytes().all(|value| value.is_ascii_hexdigit()) {
            return Err(Error::Invalid("candidate feature boundary identity".into()));
        }
        if market.configuration_hash() != self.market_hash {
            return Err(Error::Conflict(
                "candidate feature market configuration differs".into(),
            ));
        }
        if self.snapshot.as_ref().is_some_and(|snapshot| {
            snapshot.sequence == boundary.sequence && snapshot.boundary_id == boundary.id
        }) {
            return Ok(false);
        }
        let expected = self
            .snapshot
            .as_ref()
            .map_or(Some(1), |snapshot| snapshot.sequence.checked_add(1));
        if expected != Some(boundary.sequence) {
            return Err(Error::Conflict(
                "candidate feature boundary skipped or reordered".into(),
            ));
        }
        let result = (|| {
            let scope = market.source_scope();
            let input = boundary.input(String::new());
            if input.available_at_ns > input.evaluated_at_ns
                || input.event_time_ns > input.evaluated_at_ns
            {
                return Err(Error::Invalid(
                    "candidate feature clock is in the future".into(),
                ));
            }
            if let Kind::Trade { observation, .. } = &boundary.kind {
                if observation.key.provider != scope.provider
                    || observation.key.instrument != scope.instrument
                    || observation.key.session != scope.session
                {
                    return Err(Error::Conflict("candidate feature trade scope".into()));
                }
            }
            let updated = self.macd.observe_boundary(boundary, market)?;
            let mut macd = updated.clone().or_else(|| self.macd.reading().cloned());
            if updated.is_none() {
                if let Some(reading) = &mut macd {
                    reading.completed_reversal = false;
                    reading.episode_started = false;
                }
            }
            let one_second = if let Kind::Completed {
                interval_ns: SECOND,
                bar,
                ..
            } = &boundary.kind
            {
                // `true` means this is a newly consumed source candle, not that
                // its feed is timely. Entry freshness remains an independent gate.
                if !self
                    .setup
                    .observe(scope.session, &bar.bar, &self.config.setup, true)?
                    || !self.activity.observe(scope.session, &bar.bar, true)?
                {
                    return Err(Error::Conflict(
                        "completed feature candle did not advance".into(),
                    ));
                }
                let bars = market.market()?.completed();
                let previous_bar_end_ns = bars
                    .len()
                    .checked_sub(2)
                    .map(|index| bars[index].bar.end_ns)
                    .filter(|end| *end == bar.bar.start_ns);
                Some(OneSecond {
                    at_ns: bar.bar.end_ns,
                    previous_bar_end_ns,
                    range: self.setup.prior_range.clone(),
                    range_activity: self
                        .activity
                        .range(bar.bar.end_ns, self.config.minimum_range_pct)?,
                    progress_activity: self
                        .activity
                        .progress(bar.bar.end_ns, self.config.minimum_progress_pct)?,
                    vwap: bar.session_vwap,
                    high: bar.session_high,
                    prior_high: bar.prior_session_high,
                    levels: market.strategy_levels(bar.bar.end_ns, self.config.maximum_levels)?,
                })
            } else {
                None
            };
            self.snapshot = Some(Snapshot {
                boundary_id: boundary.id.into(),
                sequence: boundary.sequence,
                available_at_ns: input.available_at_ns,
                evaluated_at_ns: input.evaluated_at_ns,
                macd,
                one_second,
            });
            Ok(true)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
}
