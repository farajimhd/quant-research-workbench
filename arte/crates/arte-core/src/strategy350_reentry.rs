//! Strategy 350 successor reentry guard. Position history advances on fills;
//! high/crossing evidence advances on eligible canonical trades only.
//! Port references: frozen early_squeeze_momentum.py, early_squeeze_breakout.py
//! and the target-fill callback in strategy_engine.py. No order authority.
use crate::{
    content_hash,
    event_order::Scope,
    events::{Observation, Payload},
    execution_events::{Direction, Fill, Leg},
    execution_interval::ExecutionInterval,
    trade_eligibility::Pinned,
    Error, Result,
};
use serde::{Deserialize, Serialize};

const SECOND: u64 = 1_000_000_000;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub price_scale: u8,
    pub trade_policy_hash: String,
    pub rapid_window_ns: u64,
    pub target_candle_ns: u64,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        if self.execution_interval != ExecutionInterval::Events
            || self.price_scale > 9
            || self.rapid_window_ns != 10 * SECOND
            || self.target_candle_ns != SECOND
            || self.trade_policy_hash.len() != 64
            || !self
                .trade_policy_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("Strategy 350 reentry configuration".into()));
        }
        content_hash(&("arte.strategy-350-reentry-config.v1", self))
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Active {
    entry_resistance_id: String,
    resistance_high_atoms: i64,
    quantity: u64,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Previous {
    pub closed_at_ns: u64,
    pub entry_resistance_id: String,
    pub resistance_high_atoms: i64,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Approval {
    event_hash: String,
    resistance_id: String,
    evaluated_at_ns: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    Allowed,
    TargetCandleBlocked,
    PriorHighCrossRequired { rapid: bool, same_resistance: bool },
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    configuration_hash: String,
    scope: Scope,
    account: String,
    run_id: String,
    session_start_ns: u64,
    session_end_ns: u64,
    active: Option<Active>,
    previous: Option<Previous>,
    previous_reentry_trade_atoms: Option<i64>,
    approved: Option<Approval>,
    target_not_before_ns: u64,
    last_trade_order: Option<(u64, u64)>,
    last_trade_hash: Option<String>,
    last_fill_sequence: u64,
}
impl State {
    pub fn new(
        config: &Config,
        scope: Scope,
        account: String,
        run_id: String,
        session_start_ns: u64,
        session_end_ns: u64,
    ) -> Result<Self> {
        let configuration_hash = config.hash()?;
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || account.is_empty()
            || run_id.is_empty()
            || session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(SECOND)
            || !session_end_ns.is_multiple_of(SECOND)
        {
            return Err(Error::Invalid("Strategy 350 reentry owner".into()));
        }
        Ok(Self {
            configuration_hash,
            scope,
            account,
            run_id,
            session_start_ns,
            session_end_ns,
            active: None,
            previous: None,
            previous_reentry_trade_atoms: None,
            approved: None,
            target_not_before_ns: 0,
            last_trade_order: None,
            last_trade_hash: None,
            last_fill_sequence: 0,
        })
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn account(&self) -> &str {
        &self.account
    }
    pub fn run_id(&self) -> &str {
        &self.run_id
    }
    pub fn validate(&self, config: &Config) -> Result<()> {
        if self.configuration_hash != config.hash()?
            || self.scope.provider == 0
            || self.scope.instrument == 0
            || !(19000101..=29991231).contains(&self.scope.session)
            || self.account.is_empty()
            || self.run_id.is_empty()
            || self.session_start_ns == 0
            || self.session_start_ns >= self.session_end_ns
            || !self.session_start_ns.is_multiple_of(SECOND)
            || !self.session_end_ns.is_multiple_of(SECOND)
            || self.last_trade_order.is_some() != self.last_trade_hash.is_some()
            || self.last_trade_hash.as_ref().is_some_and(|hash| {
                hash.len() != 64
                    || !hash
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
            || self.last_trade_order.is_some_and(|order| {
                order.0 < self.session_start_ns || order.0 >= self.session_end_ns || order.1 == 0
            })
            || self.target_not_before_ns > self.session_end_ns
            || (self.target_not_before_ns != 0
                && !self
                    .target_not_before_ns
                    .is_multiple_of(config.target_candle_ns))
            || self.active.as_ref().is_some_and(|active| {
                active.quantity == 0
                    || active.resistance_high_atoms <= 0
                    || active.entry_resistance_id.is_empty()
                    || active.entry_resistance_id.len() > 128
            })
            || self.previous.as_ref().is_some_and(|previous| {
                previous.closed_at_ns < self.session_start_ns
                    || previous.closed_at_ns >= self.session_end_ns
                    || previous.resistance_high_atoms <= 0
                    || previous.entry_resistance_id.is_empty()
                    || previous.entry_resistance_id.len() > 128
            })
            || self
                .previous_reentry_trade_atoms
                .is_some_and(|price| price <= 0)
            || (self.previous_reentry_trade_atoms.is_some() && self.previous.is_none())
            || ((self.active.is_some() || self.previous.is_some())
                && (self.last_fill_sequence == 0 || self.last_trade_order.is_none()))
            || self.approved.as_ref().is_some_and(|approved| {
                self.active.is_some()
                    || approved.resistance_id.is_empty()
                    || approved.resistance_id.len() > 128
                    || approved.event_hash != self.last_trade_hash.clone().unwrap_or_default()
                    || approved.evaluated_at_ns < self.session_start_ns
                    || approved.evaluated_at_ns >= self.session_end_ns
            })
        {
            return Err(Error::Conflict(
                "Strategy 350 reentry recovery state".into(),
            ));
        }
        Ok(())
    }
    pub fn previous(&self) -> Option<&Previous> {
        self.previous.as_ref()
    }
    pub fn target_not_before_ns(&self) -> u64 {
        self.target_not_before_ns
    }
    pub fn active(&self) -> bool {
        self.active.is_some()
    }
    fn fill(&self, fill: &Fill) -> Result<()> {
        fill.id()?;
        if fill.account != self.account
            || fill.instrument != self.scope.instrument
            || fill.price_scale > 9
            || fill.sequence <= self.last_fill_sequence
            || fill.at_ns < self.session_start_ns
            || fill.at_ns >= self.session_end_ns
            || matches!(&fill.origin, crate::execution_events::Origin::Simulated { run_id, .. } if run_id != &self.run_id)
        {
            return Err(Error::Conflict("Strategy 350 fill owner or order".into()));
        }
        Ok(())
    }
    fn trade(
        &self,
        event: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
        config: &Config,
        allow_same: bool,
    ) -> Result<(i64, String)> {
        event.validate()?;
        let event_hash = content_hash(event)?;
        let order = (event.sip.ns, event.key.sequence);
        if self.configuration_hash != config.hash()?
            || policy.hash() != config.trade_policy_hash
            || event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
            || event.sip.ns < self.session_start_ns
            || event.sip.ns >= self.session_end_ns
            || event.available_at_ns > evaluated_at_ns
            || self.last_trade_order.is_some_and(|last| {
                order < last
                    || (order == last
                        && (!allow_same
                            || self.last_trade_hash.as_deref() != Some(event_hash.as_str())))
            })
            || !policy.evaluate(event, evaluated_at_ns)?
        {
            return Err(Error::Unready(
                "Strategy 350 eligible reentry trade missing".into(),
            ));
        }
        let Payload::Trade { price, .. } = &event.payload else {
            return Err(Error::Invalid("Strategy 350 reentry requires trade".into()));
        };
        let atoms = price.atoms_at_scale(config.price_scale)?;
        if atoms <= 0 {
            return Err(Error::Invalid("Strategy 350 reentry trade price".into()));
        }
        Ok((atoms, event_hash))
    }
    /// Only a matched, actual first entry fill starts a position lifecycle.
    /// `entry_trade` is the causal trade that produced the entry request.
    pub fn record_entry_fill(
        &mut self,
        fill: &Fill,
        entry_resistance_id: &str,
        entry_trade: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
        config: &Config,
    ) -> Result<()> {
        self.fill(fill)?;
        if self.configuration_hash != config.hash()?
            || fill.leg != Leg::Entry
            || fill.direction != Direction::Buy
            || self.active.is_some()
            || fill.price_scale != config.price_scale
            || entry_resistance_id.is_empty()
            || entry_resistance_id.len() > 128
            || entry_trade.available_at_ns > fill.at_ns
            || evaluated_at_ns > fill.at_ns
        {
            return Err(Error::Conflict("Strategy 350 first entry fill".into()));
        }
        let (price, event_hash) = self.trade(entry_trade, policy, evaluated_at_ns, config, true)?;
        if self.approved.as_ref().is_none_or(|approved| {
            approved.event_hash != event_hash
                || approved.resistance_id != entry_resistance_id
                || approved.evaluated_at_ns != evaluated_at_ns
        }) {
            return Err(Error::Unready(
                "Strategy 350 entry lacks approved reentry trade".into(),
            ));
        }
        self.active = Some(Active {
            entry_resistance_id: entry_resistance_id.into(),
            resistance_high_atoms: price,
            quantity: fill.quantity,
        });
        self.last_trade_order = Some((entry_trade.sip.ns, entry_trade.key.sequence));
        self.last_trade_hash = Some(event_hash);
        self.last_fill_sequence = fill.sequence;
        self.approved = None;
        Ok(())
    }
    pub fn record_add_fill(&mut self, fill: &Fill, config: &Config) -> Result<()> {
        self.fill(fill)?;
        if self.configuration_hash != config.hash()?
            || fill.price_scale != config.price_scale
            || fill.leg != Leg::Entry
            || fill.direction != Direction::Buy
        {
            return Err(Error::Conflict("Strategy 350 add fill lifecycle".into()));
        }
        let active = self
            .active
            .as_mut()
            .ok_or_else(|| Error::Unready("Strategy 350 add without position".into()))?;
        active.quantity = active
            .quantity
            .checked_add(fill.quantity)
            .ok_or_else(|| Error::Capacity("Strategy 350 position quantity".into()))?;
        self.last_fill_sequence = fill.sequence;
        Ok(())
    }
    /// Update the prior-position resistance high from an eligible trade.
    pub fn observe_held_trade(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
        config: &Config,
    ) -> Result<()> {
        if self.active.is_none() {
            return Err(Error::Unready("Strategy 350 held position absent".into()));
        }
        let (price, event_hash) = self.trade(event, policy, evaluated_at_ns, config, false)?;
        let active = self.active.as_mut().unwrap();
        active.resistance_high_atoms = active.resistance_high_atoms.max(price);
        self.last_trade_order = Some((event.sip.ns, event.key.sequence));
        self.last_trade_hash = Some(event_hash);
        Ok(())
    }
    /// A target fill blocks reentry until the next exact one-second candle.
    /// Only fills that exhaust the account-owned quantity freeze the position.
    pub fn record_exit_fill(&mut self, fill: &Fill, config: &Config) -> Result<()> {
        self.fill(fill)?;
        if self.configuration_hash != config.hash()?
            || self.active.is_none()
            || fill.price_scale != config.price_scale
            || fill.direction != Direction::Sell
            || !matches!(fill.leg, Leg::Stop | Leg::Target | Leg::Exit)
        {
            return Err(Error::Conflict("Strategy 350 exit fill lifecycle".into()));
        }
        let remaining = self
            .active
            .as_ref()
            .unwrap()
            .quantity
            .checked_sub(fill.quantity)
            .ok_or_else(|| Error::Conflict("Strategy 350 exit exceeds held quantity".into()))?;
        if fill.leg == Leg::Target {
            let next = (fill.at_ns / config.target_candle_ns)
                .checked_add(1)
                .and_then(|bucket| bucket.checked_mul(config.target_candle_ns))
                .ok_or_else(|| Error::Capacity("Strategy 350 target candle clock".into()))?;
            self.target_not_before_ns = self.target_not_before_ns.max(next);
        }
        if remaining == 0 {
            let active = self.active.take().unwrap();
            self.previous = Some(Previous {
                closed_at_ns: fill.at_ns,
                entry_resistance_id: active.entry_resistance_id,
                resistance_high_atoms: active.resistance_high_atoms,
            });
            self.previous_reentry_trade_atoms = None;
            self.approved = None;
        } else {
            self.active.as_mut().unwrap().quantity = remaining;
        }
        self.last_fill_sequence = fill.sequence;
        Ok(())
    }
    /// A rejected crossing still advances the prior trade, exactly as the
    /// source strategy does. Invalid evidence never changes state.
    pub fn evaluate_flat_trade(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
        proposed_resistance_id: &str,
        config: &Config,
    ) -> Result<Gate> {
        if self.active.is_some()
            || proposed_resistance_id.is_empty()
            || proposed_resistance_id.len() > 128
        {
            return Err(Error::Conflict("Strategy 350 flat reentry scope".into()));
        }
        let (price, event_hash) = self.trade(event, policy, evaluated_at_ns, config, false)?;
        let result = if evaluated_at_ns < self.target_not_before_ns {
            Gate::TargetCandleBlocked
        } else if let Some(previous) = &self.previous {
            if previous.closed_at_ns > evaluated_at_ns {
                return Err(Error::Conflict(
                    "Strategy 350 prior close follows trade".into(),
                ));
            }
            let rapid = evaluated_at_ns >= previous.closed_at_ns
                && evaluated_at_ns - previous.closed_at_ns < config.rapid_window_ns;
            let same_resistance = previous.entry_resistance_id == proposed_resistance_id;
            if (rapid || same_resistance)
                && !self.previous_reentry_trade_atoms.is_some_and(|prior| {
                    prior <= previous.resistance_high_atoms
                        && previous.resistance_high_atoms < price
                })
            {
                self.previous_reentry_trade_atoms = Some(price);
                Gate::PriorHighCrossRequired {
                    rapid,
                    same_resistance,
                }
            } else {
                if rapid || same_resistance {
                    self.previous_reentry_trade_atoms = Some(price);
                }
                Gate::Allowed
            }
        } else {
            Gate::Allowed
        };
        self.approved = (result == Gate::Allowed).then(|| Approval {
            event_hash: event_hash.clone(),
            resistance_id: proposed_resistance_id.into(),
            evaluated_at_ns,
        });
        self.last_trade_order = Some((event.sip.ns, event.key.sequence));
        self.last_trade_hash = Some(event_hash);
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        events::{Decimal, EventKey, EventKind, SourceTime},
        execution_events::Origin,
        trade_eligibility::Policy,
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn policy() -> Pinned {
        let policy = Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 0,
            valid_to_ns: u64::MAX,
            available_at_ns: 0,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: BTreeSet::new(),
            excluded_conditions: BTreeSet::new(),
            allow_empty_conditions: true,
        };
        let hash = policy.hash().unwrap();
        Pinned::new(policy, &hash).unwrap()
    }
    fn config(policy: &Pinned) -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            trade_policy_hash: policy.hash().into(),
            rapid_window_ns: 10 * S,
            target_candle_ns: S,
        }
    }
    fn event(at: u64, sequence: u64, price: &str) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence,
            },
            payload: Payload::Trade {
                price: Decimal::parse(price).unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions: Vec::new(),
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at,
            receipt: None,
        }
    }
    fn fill(at: u64, sequence: u64, leg: Leg, direction: Direction, quantity: u64) -> Fill {
        Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "run".into(),
                model: "fill-v1".into(),
            },
            command_id: format!("command-{sequence}"),
            account: "first".into(),
            instrument: 10,
            price_scale: 2,
            sequence,
            at_ns: at,
            executed_at_ns: None,
            leg,
            direction,
            quantity,
            price: 1000,
        }
    }
    fn state(config: &Config) -> State {
        State::new(
            config,
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            "first".into(),
            "run".into(),
            S,
            100 * S,
        )
        .unwrap()
    }
    #[test]
    fn target_fill_and_actual_flat_exit_control_causal_reentry() {
        let policy = policy();
        let config = config(&policy);
        let mut state = state(&config);
        let first = event(2 * S, 1, "10");
        assert_eq!(
            state
                .evaluate_flat_trade(&first, &policy, 2 * S, "r1", &config)
                .unwrap(),
            Gate::Allowed
        );
        assert!(state
            .record_entry_fill(
                &fill(3 * S, 1, Leg::Entry, Direction::Buy, 2),
                "r2",
                &first,
                &policy,
                2 * S,
                &config
            )
            .is_err());
        state
            .record_entry_fill(
                &fill(3 * S, 1, Leg::Entry, Direction::Buy, 2),
                "r1",
                &first,
                &policy,
                2 * S,
                &config,
            )
            .unwrap();
        state
            .observe_held_trade(&event(4 * S, 2, "10.50"), &policy, 4 * S, &config)
            .unwrap();
        state
            .record_exit_fill(&fill(5 * S, 2, Leg::Target, Direction::Sell, 1), &config)
            .unwrap();
        assert!(state.active());
        state
            .record_exit_fill(
                &fill(5 * S + S / 2, 3, Leg::Target, Direction::Sell, 1),
                &config,
            )
            .unwrap();
        assert!(!state.active());
        assert_eq!(state.previous().unwrap().resistance_high_atoms, 1050);
        assert_eq!(state.target_not_before_ns(), 6 * S);
        assert_eq!(
            state
                .evaluate_flat_trade(
                    &event(5 * S + 800_000_000, 3, "10.40"),
                    &policy,
                    5 * S + 800_000_000,
                    "r1",
                    &config
                )
                .unwrap(),
            Gate::TargetCandleBlocked
        );
        assert_eq!(
            state
                .evaluate_flat_trade(&event(6 * S, 4, "10.40"), &policy, 6 * S, "r1", &config)
                .unwrap(),
            Gate::PriorHighCrossRequired {
                rapid: true,
                same_resistance: true
            }
        );
        let cross = event(7 * S, 5, "10.60");
        assert_eq!(
            state
                .evaluate_flat_trade(&cross, &policy, 7 * S, "r1", &config)
                .unwrap(),
            Gate::Allowed
        );
        assert!(state
            .record_entry_fill(
                &fill(7 * S + S / 2, 4, Leg::Entry, Direction::Buy, 1),
                "r2",
                &cross,
                &policy,
                7 * S,
                &config
            )
            .is_err());
        state
            .record_entry_fill(
                &fill(7 * S + S / 2, 4, Leg::Entry, Direction::Buy, 1),
                "r1",
                &cross,
                &policy,
                7 * S,
                &config,
            )
            .unwrap();
        state
            .record_exit_fill(&fill(8 * S, 5, Leg::Stop, Direction::Sell, 1), &config)
            .unwrap();
        assert_eq!(
            state
                .evaluate_flat_trade(&event(20 * S, 6, "10.50"), &policy, 20 * S, "r1", &config)
                .unwrap(),
            Gate::PriorHighCrossRequired {
                rapid: false,
                same_resistance: true
            }
        );
        assert_eq!(
            state
                .evaluate_flat_trade(&event(21 * S, 7, "10.70"), &policy, 21 * S, "r1", &config)
                .unwrap(),
            Gate::Allowed
        );
    }
    #[test]
    fn invalid_fill_and_trade_do_not_advance_owner_state() {
        let policy = policy();
        let config = config(&policy);
        let mut state = state(&config);
        let first = event(2 * S, 1, "10");
        assert_eq!(
            state
                .evaluate_flat_trade(&first, &policy, 2 * S, "r1", &config)
                .unwrap(),
            Gate::Allowed
        );
        let mut wrong_account = fill(3 * S, 1, Leg::Entry, Direction::Buy, 1);
        wrong_account.account = "other".into();
        assert!(state
            .record_entry_fill(&wrong_account, "r1", &first, &policy, 2 * S, &config)
            .is_err());
        state
            .record_entry_fill(
                &fill(3 * S, 1, Leg::Entry, Direction::Buy, 1),
                "r1",
                &first,
                &policy,
                2 * S,
                &config,
            )
            .unwrap();
        assert!(state
            .record_exit_fill(&fill(4 * S, 2, Leg::Exit, Direction::Sell, 2), &config)
            .is_err());
        assert!(state.active());
        state
            .record_exit_fill(&fill(4 * S, 2, Leg::Exit, Direction::Sell, 1), &config)
            .unwrap();
        assert!(state
            .evaluate_flat_trade(&first, &policy, 4 * S, "r2", &config)
            .is_err());
    }
    #[test]
    fn rehashed_impossible_recovery_state_is_rejected() {
        let policy = policy();
        let config = config(&policy);
        let mut state = state(&config);
        let first = event(2 * S, 1, "10");
        state
            .evaluate_flat_trade(&first, &policy, 2 * S, "r1", &config)
            .unwrap();
        state
            .record_entry_fill(
                &fill(3 * S, 1, Leg::Entry, Direction::Buy, 1),
                "r1",
                &first,
                &policy,
                2 * S,
                &config,
            )
            .unwrap();
        state.validate(&config).unwrap();
        let mut changed = state.clone();
        changed.active.as_mut().unwrap().quantity = 0;
        let image = serde_json::to_vec(&changed).unwrap();
        assert_eq!(content_hash(&changed).unwrap().len(), 64);
        let restored: State = serde_json::from_slice(&image).unwrap();
        assert!(restored.validate(&config).is_err());
    }
}
