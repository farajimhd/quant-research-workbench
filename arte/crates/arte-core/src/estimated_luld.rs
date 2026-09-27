//! Historical-only 500 ms modeled bands. These are never official OMS evidence.
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const CONTRACT: &str = "arte.estimated-luld-500ms.v1";
pub const STEP_NS: u64 = 500_000_000;
const WINDOW_NS: u64 = 300_000_000_000;
const REFRESH_NS: u64 = 30_000_000_000;

/// Prices are integer ten-thousandths. Sum is of eligible transaction prices,
/// not size-weighted notional. The caller certifies trade eligibility.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TradeBucket {
    pub start_ns: u64,
    pub price_sum: u128,
    pub count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Policy {
    /// Explicit modeled percentage parameter; never inferred from price alone.
    pub width_bps: u32,
    pub minimum_trades: u32,
    pub maximum_buckets: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Point {
    pub end_ns: u64,
    pub lower: i64,
    pub upper: i64,
    pub reference: i64,
    pub sample_count: u64,
}

/// Emits only changes. The pinned coverage declares a complete 500 ms grid;
/// the value carries forward until another point or an explicit unknown.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Change {
    pub end_ns: u64,
    pub value: Option<Point>,
}

fn rounded_cent(value: u128) -> Result<i64> {
    let cents = value
        .checked_add(50)
        .ok_or_else(|| Error::Invalid("modeled band overflow".into()))?
        / 100;
    let scaled = cents
        .checked_mul(100)
        .ok_or_else(|| Error::Invalid("modeled band overflow".into()))?;
    i64::try_from(scaled).map_err(|_| Error::Invalid("modeled band overflow".into()))
}

/// A deliberately labeled approximation: first eligible window seeds the
/// reference; subsequent 30-second checks refresh only after a 1% move.
/// Opening auction, tier selection, pause/reopening and official SIP publication
/// are outside this model and must be supplied by future versioned evidence.
pub fn project(
    start_ns: u64,
    end_ns: u64,
    buckets: &[TradeBucket],
    policy: Policy,
) -> Result<Vec<Change>> {
    if start_ns >= end_ns
        || end_ns > u64::MAX - WINDOW_NS
        || start_ns % STEP_NS != 0
        || end_ns % STEP_NS != 0
        || (end_ns - start_ns) / STEP_NS > policy.maximum_buckets as u64
        || policy.maximum_buckets == 0
        || policy.minimum_trades == 0
        || policy.width_bps == 0
        || policy.width_bps >= 10_000
    {
        return Err(Error::Invalid("estimated LULD scope or policy".into()));
    }
    let mut previous = None;
    for bucket in buckets {
        if bucket.start_ns < start_ns
            || bucket.start_ns >= end_ns
            || bucket.start_ns % STEP_NS != 0
            || bucket.count == 0
            || bucket.price_sum == 0
            || previous.is_some_and(|prior| bucket.start_ns <= prior)
        {
            return Err(Error::Invalid("estimated LULD trade bucket".into()));
        }
        previous = Some(bucket.start_ns);
    }
    let mut queue: VecDeque<TradeBucket> = VecDeque::new();
    let mut sum: u128 = 0;
    let mut count: u64 = 0;
    let mut index = 0;
    let mut reference: Option<u128> = None;
    let mut refreshed_at = 0;
    let mut last: Option<(i64, i64, i64, u64)> = None;
    let mut changes = Vec::new();
    for step in 0..((end_ns - start_ns) / STEP_NS) {
        let boundary = start_ns + (step + 1) * STEP_NS;
        while index < buckets.len() && buckets[index].start_ns < boundary {
            let bucket = buckets[index];
            sum = sum
                .checked_add(bucket.price_sum)
                .ok_or_else(|| Error::Invalid("estimated LULD price sum overflow".into()))?;
            count = count
                .checked_add(u64::from(bucket.count))
                .ok_or_else(|| Error::Invalid("estimated LULD count overflow".into()))?;
            queue.push_back(bucket);
            index += 1;
        }
        while queue
            .front()
            .is_some_and(|bucket| bucket.start_ns + WINDOW_NS < boundary)
        {
            let bucket = queue.pop_front().expect("front checked");
            sum -= bucket.price_sum;
            count -= u64::from(bucket.count);
        }
        let candidate = if count >= u64::from(policy.minimum_trades) {
            Some(sum / u128::from(count))
        } else {
            None
        };
        if let Some(candidate) = candidate {
            if reference.is_none() {
                reference = Some(candidate);
                refreshed_at = boundary;
            } else if boundary - refreshed_at >= REFRESH_NS {
                let old = reference.expect("reference checked");
                let delta = candidate.abs_diff(old);
                if delta.checked_mul(100).is_some_and(|scaled| scaled >= old) {
                    reference = Some(candidate);
                }
                refreshed_at = boundary;
            }
        } else {
            reference = None;
        }
        let current = if let Some(reference) = reference {
            let lower = rounded_cent(
                reference
                    .checked_mul(u128::from(10_000 - policy.width_bps))
                    .ok_or_else(|| Error::Invalid("modeled lower overflow".into()))?
                    / 10_000,
            )?;
            let upper = rounded_cent(
                reference
                    .checked_mul(u128::from(10_000 + policy.width_bps))
                    .ok_or_else(|| Error::Invalid("modeled upper overflow".into()))?
                    / 10_000,
            )?;
            let reference = i64::try_from(reference)
                .map_err(|_| Error::Invalid("modeled reference overflow".into()))?;
            Some((lower, upper, reference, count))
        } else {
            None
        };
        if current != last {
            changes.push(Change {
                end_ns: boundary,
                value: current.map(|(lower, upper, reference, sample_count)| Point {
                    end_ns: boundary,
                    lower,
                    upper,
                    reference,
                    sample_count,
                }),
            });
            last = current;
        }
    }
    Ok(changes)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> Policy {
        Policy {
            width_bps: 500,
            minimum_trades: 1,
            maximum_buckets: 1000,
        }
    }
    #[test]
    fn sparse_500ms_bands_are_modeled_and_expire() {
        let start = 1_000_000_000_000;
        let rows = [TradeBucket {
            start_ns: start,
            price_sum: 100_0000,
            count: 1,
        }];
        let changes = project(
            start,
            start + 301_000_000_000,
            &rows,
            Policy {
                maximum_buckets: 700,
                ..policy()
            },
        )
        .unwrap();
        assert_eq!(changes[0].end_ns, start + STEP_NS);
        assert_eq!(changes[0].value.unwrap().lower, 950_000);
        assert_eq!(changes[0].value.unwrap().upper, 1_050_000);
        assert_eq!(changes.last().unwrap().value, None);
    }
    #[test]
    fn rejects_duplicate_or_unaligned_inputs() {
        let start = 1_000_000_000_000;
        let row = TradeBucket {
            start_ns: start,
            price_sum: 100_0000,
            count: 1,
        };
        assert!(project(start, start + STEP_NS, &[row, row], policy()).is_err());
        assert!(project(start + 1, start + STEP_NS, &[row], policy()).is_err());
    }

    #[test]
    fn later_trade_cannot_change_prior_bucket_and_refresh_is_delayed() {
        let start = 1_000_000_000_000;
        let rows = [
            TradeBucket {
                start_ns: start,
                price_sum: 1_000_000,
                count: 1,
            },
            TradeBucket {
                start_ns: start + 29_500_000_000,
                price_sum: 1_100_000,
                count: 1,
            },
        ];
        let changes = project(start, start + 31_000_000_000, &rows, policy()).unwrap();
        let refreshed = changes
            .iter()
            .find(|row| row.value.is_some_and(|p| p.reference != 1_000_000))
            .unwrap();
        assert_eq!(refreshed.end_ns, start + 30_500_000_000);
        assert_eq!(refreshed.value.unwrap().reference, 1_050_000);
        assert!(changes
            .iter()
            .filter(|row| row.end_ns < refreshed.end_ns)
            .all(|row| row.value.is_some_and(|p| p.reference == 1_000_000)));
    }
}
