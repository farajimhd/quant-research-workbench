//! Causal reaction prominence, independent of structural membership.
//!
//! Revision 1 uses the mean true range of the last 14 completed populated
//! one-minute buckets, floored by the price tick at contact. One frozen range
//! is the minimum departure before a return can begin another encounter.
//! No evidence arrays are retained. Callers supply the accepted-break and role
//! state from the structural engine; price penetration alone is not acceptance.
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const PROMINENCE_REVISION: &str = "reaction-prominence-1";

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CompactLevelState {
    pub episode_id: u64,
    pub observed_from_ms: i64,
    pub reaction: ReactionProminence,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CompactBookState {
    pub next_episode_id: u64,
    pub volatility: CompletedRange,
    pub current_range: Option<f64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct ReactionProminence {
    pub completed: f64,
    pub current_best: f64,
    pub frozen_range: f64,
    /// 0: waiting for contact; 1: contact without qualified departure; 2: departed.
    pub phase: u8,
    pub side: i8,
    pub completed_encounters: u64,
}

impl ReactionProminence {
    pub fn score(&self) -> f64 {
        (self.completed + self.current_best).ln_1p()
    }

    fn finish(&mut self) {
        if self.phase != 0 {
            self.completed += self.current_best;
            self.completed_encounters += 1;
        }
        self.current_best = 0.0;
        self.frozen_range = 0.0;
        self.phase = 0;
    }

    pub fn observe(&mut self, price: f64, lower: f64, upper: f64, side: i8,
                   prior_range: Option<f64>, active: bool, accepted_break: bool) {
        if accepted_break || (self.side != 0 && self.side != side) {
            self.finish();
        }
        self.side = side;
        if !active || accepted_break { return; }
        let contact = price >= lower && price <= upper;
        if self.phase == 2 && contact { self.finish(); }
        if self.phase == 0 && contact {
            if let Some(range) = prior_range.filter(|r| r.is_finite() && *r > 0.0) {
                self.frozen_range = range;
                self.phase = 1;
            }
        }
        if self.phase != 0 {
            let distance = if side > 0 { price - upper } else { lower - price };
            self.current_best = self.current_best.max(distance.max(0.0) / self.frozen_range);
            if self.current_best >= 1.0 { self.phase = 2; }
        }
    }

    pub fn apply_split(&mut self, factor: f64) {
        self.frozen_range *= factor;
        // Completed/current contributions are dimensionless and do not change.
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct CompletedRange {
    pub minute: Option<i64>,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub previous_close: Option<f64>,
    pub ranges: VecDeque<f64>,
}

impl CompletedRange {
    /// Complete the preceding bucket before returning the normalizer. The
    /// current trade never contributes to its own encounter's volatility.
    pub fn observe(&mut self, timestamp_ms: i64, price: f64) -> Option<f64> {
        let minute = timestamp_ms.div_euclid(60_000);
        if self.minute != Some(minute) {
            if self.minute.is_some() {
                let previous = self.previous_close.unwrap_or(self.close);
                self.ranges.push_back((self.high-self.low)
                    .max((self.high-previous).abs()).max((self.low-previous).abs()));
                if self.ranges.len() > 14 { self.ranges.pop_front(); }
                self.previous_close = Some(self.close);
            }
            self.minute = Some(minute);
            self.high = price;
            self.low = price;
        } else {
            self.high = self.high.max(price);
            self.low = self.low.min(price);
        }
        self.close = price;
        (!self.ranges.is_empty()).then(|| {
            let tick: f64 = if price < 1.0 { 0.0001 } else { 0.01 };
            (self.ranges.iter().sum::<f64>() / self.ranges.len() as f64).max(tick)
        })
    }

    pub fn apply_split(&mut self, factor: f64) {
        self.high *= factor;
        self.low *= factor;
        self.close *= factor;
        self.previous_close = self.previous_close.map(|p| p*factor);
        for range in &mut self.ranges { *range *= factor; }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contact_noise_does_not_repeat_evidence() {
        let mut score = ReactionProminence::default();
        for p in [10., 10.2, 10., 10.4, 10., 10.3] {
            score.observe(p, 9.9, 10.1, 1, Some(1.), true, false);
        }
        assert_eq!(score.completed_encounters, 0);
        assert!((score.current_best-0.3).abs()<1e-12);
        score.observe(12.1, 9.9, 10.1, 1, Some(5.), true, false);
        assert!((score.current_best-2.).abs()<1e-12);
        let before=score.score();
        score.observe(10., 9.9, 10.1, 1, Some(2.), true, false);
        assert_eq!(score.completed_encounters, 1);
        assert_eq!(score.frozen_range, 2.);
        assert_eq!(score.score(), before);
    }

    #[test]
    fn restart_and_split_preserve_prefix_score() {
        let mut score=ReactionProminence::default();
        score.observe(10.,9.9,10.1,1,Some(1.),true,false);
        score.observe(12.1,9.9,10.1,1,Some(1.),true,false);
        let before=score.score();
        let mut restored: ReactionProminence=serde_json::from_str(&serde_json::to_string(&score).unwrap()).unwrap();
        restored.apply_split(75.);
        assert_eq!(restored.score(),before);
        restored.observe(12.1*75.,9.9*75.,10.1*75.,1,Some(75.),true,false);
        assert!((restored.score()-before).abs()<1e-12);
        restored.observe(9.*75.,9.9*75.,10.1*75.,1,Some(75.),false,true);
        assert_eq!(restored.phase,0);
        assert_eq!(restored.completed_encounters,1);
    }

    #[test]
    fn volatility_uses_only_completed_minutes() {
        let mut range=CompletedRange::default();
        assert_eq!(range.observe(0,10.),None);
        assert_eq!(range.observe(1,12.),None);
        assert_eq!(range.observe(60_000,100.),Some(2.));
        assert_eq!(range.observe(60_001,200.),Some(2.));
        range.apply_split(0.1);
        assert!((range.observe(60_002,20.).unwrap()-0.2).abs()<1e-12);
    }
}
