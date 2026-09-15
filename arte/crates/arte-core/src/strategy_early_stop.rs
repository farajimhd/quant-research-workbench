//! Early three-green stop and fill-owned reclaim permission from historical_hod.
//! Prices here are strategy proposals. OMS remains the broker protection authority.
use crate::market::Bar;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
const SECOND: u64 = 1_000_000_000;
fn price(p: f64) -> bool {
    p.is_finite() && p > 0.
}
fn validate(b: &Bar) -> Result<()> {
    if b.end_ns.checked_sub(b.start_ns) != Some(SECOND)
        || ![b.open, b.close, b.high, b.low].into_iter().all(price)
        || b.low > b.open.min(b.close)
        || b.high < b.open.max(b.close)
    {
        return Err(Error::Invalid(
            "early stop requires valid completed one-second bar".into(),
        ));
    }
    Ok(())
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candidate {
    pub price: f64,
    pub candles: Vec<Bar>,
    pub confirmed_at_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActiveStop {
    pub candidate: Candidate,
    pub activation: Bar,
}
/// Shared market evidence; survives position and MACD episode boundaries.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GreenRun {
    previous: Option<Bar>,
    candles: VecDeque<Bar>,
}
impl GreenRun {
    pub fn observe(&mut self, bar: &Bar, tick: f64) -> Result<Option<Candidate>> {
        validate(bar)?;
        if !price(tick) {
            return Err(Error::Invalid("invalid tick".into()));
        }
        if let Some(previous) = &self.previous {
            if bar.end_ns < previous.end_ns {
                return Err(Error::Invalid("green-run clock rewind".into()));
            }
            if bar.end_ns == previous.end_ns {
                return Ok(None);
            }
        }
        let green = self.previous.as_ref().is_some_and(|p| {
            p.end_ns == bar.start_ns && bar.close > bar.open && bar.close > p.close
        });
        let stop = (bar.close / tick + 1e-9).floor() * tick;
        if !price(stop) {
            return Err(Error::Invalid("tick rounding overflow".into()));
        }
        if green {
            if self.candles.len() == 3 {
                self.candles.pop_front();
            }
            self.candles.push_back(bar.clone());
        } else {
            self.candles.clear();
        }
        self.previous = Some(bar.clone());
        Ok((self.candles.len() == 3).then(|| Candidate {
            price: (self.candles[1].close / tick + 1e-9).floor() * tick,
            candles: self.candles.iter().cloned().collect(),
            confirmed_at_ns: bar.end_ns,
        }))
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PositionStop {
    pub graduated: bool,
    pub pending: Option<Candidate>,
    pub active: Option<ActiveStop>,
    last_end_ns: u64,
}
impl PositionStop {
    /// `crossed_higher_resistance` must come from causal prior level evidence.
    pub fn observe(
        &mut self,
        bar: &Bar,
        contiguous: bool,
        crossed_higher_resistance: bool,
        candidate: Option<Candidate>,
    ) -> Result<Option<f64>> {
        validate(bar)?;
        if bar.end_ns < self.last_end_ns {
            return Err(Error::Invalid("early-stop clock rewind".into()));
        }
        if bar.end_ns == self.last_end_ns {
            return Ok(None);
        }
        if let Some(c) = &candidate {
            if c.confirmed_at_ns != bar.end_ns || !price(c.price) || c.candles.len() != 3 {
                return Err(Error::Invalid("invalid green-stop candidate".into()));
            }
            for b in &c.candles {
                validate(b)?;
            }
            if c.candles.last().unwrap().end_ns != bar.end_ns
                || c.candles.iter().any(|b| b.close <= b.open)
                || c.candles
                    .windows(2)
                    .any(|w| w[0].end_ns != w[1].start_ns || w[1].close <= w[0].close)
            {
                return Err(Error::Invalid("invalid rising green sequence".into()));
            }
        }
        self.last_end_ns = bar.end_ns;
        if self.graduated {
            return Ok(None);
        }
        if crossed_higher_resistance && bar.close >= bar.open {
            self.graduated = true;
            return Ok(None);
        }
        if self.active.is_some() {
            return Ok(None);
        }
        if !contiguous {
            self.pending = None;
        }
        if self.pending.is_none() {
            self.pending = candidate;
        }
        if bar.close < bar.open {
            if let Some(candidate) = self.pending.take() {
                let p = candidate.price;
                self.active = Some(ActiveStop {
                    candidate,
                    activation: bar.clone(),
                });
                return Ok(Some(p));
            }
        }
        Ok(None)
    }
    /// Call only on reconciled execution evidence for this account and position.
    pub fn filled(
        &self,
        active_broker_stop: f64,
        at_ns: u64,
        role: FillRole,
        managed_protective_exit: bool,
    ) -> Result<Option<Reclaim>> {
        let Some(stop) = &self.active else {
            return Ok(None);
        };
        if !price(active_broker_stop) || at_ns < stop.activation.end_ns {
            return Err(Error::Invalid("invalid early-stop fill evidence".into()));
        }
        let protective = matches!(
            role,
            FillRole::ProtectiveStop | FillRole::TrailingStop | FillRole::ProtectiveExit
        ) || role == FillRole::ManagedExit && managed_protective_exit;
        Ok(
            (protective && active_broker_stop == stop.candidate.price).then(|| Reclaim {
                stop: stop.clone(),
                stopped_at_ns: at_ns,
                below_seen: true,
                confirmation: None,
                last_at_ns: at_ns,
            }),
        )
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FillRole {
    ProtectiveStop,
    TrailingStop,
    ProtectiveExit,
    ManagedExit,
    Other,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Confirmation {
    pub at_ns: u64,
    pub close: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Reclaim {
    pub stop: ActiveStop,
    pub stopped_at_ns: u64,
    pub below_seen: bool,
    confirmation: Option<Confirmation>,
    last_at_ns: u64,
}
pub struct ReclaimFrame {
    pub at_ns: u64,
    pub price: f64,
    pub bid: f64,
    pub bar_open: Option<f64>,
    pub fresh_close: bool,
    pub detector_fresh: bool,
    pub contiguous: bool,
    pub market_data_update: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReclaimEvidence {
    pub confirmation: Confirmation,
    pub next_open: f64,
    pub validated_at_ns: u64,
    pub stop_level: f64,
}
impl Reclaim {
    pub fn observe(&mut self, f: &ReclaimFrame) -> Result<Option<ReclaimEvidence>> {
        if f.at_ns < self.last_at_ns
            || !price(f.price)
            || !price(f.bid)
            || f.bar_open.is_some_and(|p| !price(p))
        {
            return Err(Error::Invalid("invalid reclaim observation".into()));
        }
        self.last_at_ns = f.at_ns;
        let stop = self.stop.candidate.price;
        if f.price.min(f.bid) <= stop {
            self.below_seen = true;
        }
        if f.fresh_close {
            self.confirmation = None;
            if f.at_ns > self.stopped_at_ns
                && f.detector_fresh
                && f.contiguous
                && self.below_seen
                && f.price > stop
                && f.bar_open.is_some_and(|p| f.price >= p)
            {
                self.confirmation = Some(Confirmation {
                    at_ns: f.at_ns,
                    close: f.price,
                });
                self.below_seen = false;
            }
            return Ok(None);
        }
        if !f.market_data_update || f.bar_open.is_none() {
            return Ok(None);
        }
        let Some(c) = self.confirmation.take() else {
            return Ok(None);
        };
        // Consume even failed opportunities; later favorable ticks cannot retry.
        if f.at_ns < c.at_ns
            || f.at_ns - c.at_ns >= SECOND
            || f.bar_open.unwrap() <= stop
            || f.price.min(f.bid) <= stop
        {
            return Ok(None);
        }
        Ok(Some(ReclaimEvidence {
            confirmation: c,
            next_open: f.bar_open.unwrap(),
            validated_at_ns: f.at_ns,
            stop_level: stop,
        }))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn bar(t: u64, o: f64, c: f64) -> Bar {
        Bar {
            start_ns: t * SECOND,
            end_ns: (t + 1) * SECOND,
            open: o,
            close: c,
            high: o.max(c),
            low: o.min(c),
            volume: 1.,
            notional: c,
            trades: 1,
        }
    }
    fn armed() -> PositionStop {
        let mut run = GreenRun::default();
        let mut state = PositionStop::default();
        for i in 0..4 {
            let b = bar(i, 10. + i as f64, 10.5 + i as f64);
            let c = run.observe(&b, 0.01).unwrap();
            state.observe(&b, i > 0, false, c).unwrap();
        }
        assert!(state.pending.is_some());
        assert_eq!(
            state
                .observe(&bar(4, 13.5, 13.), true, false, None)
                .unwrap(),
            Some(12.5)
        );
        state
    }
    #[test]
    fn requires_fill_of_actual_still_active_stop() {
        let mut s = armed();
        s.graduated = true;
        assert!(s
            .filled(12.6, 6 * SECOND, FillRole::ProtectiveStop, false)
            .unwrap()
            .is_none());
        assert!(s
            .filled(12.5, 6 * SECOND, FillRole::Other, false)
            .unwrap()
            .is_none());
        assert!(s
            .filled(12.5, 6 * SECOND, FillRole::ProtectiveStop, false)
            .unwrap()
            .is_some());
    }
    #[test]
    fn gap_disarms_pattern_before_red() {
        let mut s = armed();
        let candidate = s.active.take().unwrap().candidate;
        s.pending = Some(candidate);
        assert!(s
            .observe(&bar(8, 13., 12.), false, false, None)
            .unwrap()
            .is_none());
        assert!(s.pending.is_none());
    }
    #[test]
    fn opening_is_consumed_even_when_price_rejects() {
        let mut r = armed()
            .filled(12.5, 6 * SECOND, FillRole::ProtectiveStop, false)
            .unwrap()
            .unwrap();
        let mut f = ReclaimFrame {
            at_ns: 7 * SECOND,
            price: 13.,
            bid: 12.9,
            bar_open: Some(12.8),
            fresh_close: true,
            detector_fresh: true,
            contiguous: true,
            market_data_update: false,
        };
        assert!(r.observe(&f).unwrap().is_none());
        f.fresh_close = false;
        f.market_data_update = true;
        f.bar_open = Some(12.5);
        assert!(r.observe(&f).unwrap().is_none());
        f.bar_open = Some(13.);
        assert!(r.observe(&f).unwrap().is_none());
    }
    #[test]
    fn reclaim_window_half_open_and_exact_evidence() {
        for offset in [0, SECOND - 1, SECOND] {
            let mut r = armed()
                .filled(12.5, 6 * SECOND, FillRole::ProtectiveStop, false)
                .unwrap()
                .unwrap();
            let mut f = ReclaimFrame {
                at_ns: 7 * SECOND,
                price: 13.,
                bid: 12.9,
                bar_open: Some(12.8),
                fresh_close: true,
                detector_fresh: true,
                contiguous: true,
                market_data_update: false,
            };
            r.observe(&f).unwrap();
            f.at_ns += offset;
            f.fresh_close = false;
            f.market_data_update = true;
            assert_eq!(r.observe(&f).unwrap().is_some(), offset < SECOND);
        }
    }
}
