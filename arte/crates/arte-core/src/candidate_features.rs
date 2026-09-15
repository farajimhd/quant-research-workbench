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
use serde::Serialize;
const SECOND: u64 = 1_000_000_000;
#[derive(Serialize)]
pub struct Config {
    pub setup: SetupSettings,
    pub forming_macd: bool,
    pub minimum_range_pct: f64,
    pub minimum_progress_pct: f64,
}
#[derive(Debug, Clone, Serialize)]
pub struct OneSecond {
    pub at_ns: u64,
    pub previous_bar_end_ns: Option<u64>,
    pub range: Option<Range>,
    pub range_activity: ActivityEvidence,
    pub progress_activity: ActivityEvidence,
    pub vwap: f64,
    pub high: f64,
    pub prior_high: Option<f64>,
}
#[derive(Debug, Clone, Serialize)]
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
                "candidate-market-features-v1",
                market.configuration_hash(),
                &config,
            ))?,
            market_hash: market.configuration_hash().into(),
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
    pub fn snapshot(&self) -> Result<Option<&Snapshot>> {
        if self.failed {
            return Err(Error::Unready("candidate features require recovery".into()));
        }
        Ok(self.snapshot.as_ref())
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
