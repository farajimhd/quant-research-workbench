use crate::market::{Bar, BarBuilder, Update};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayTrade {
    pub instrument: u64,
    pub sip_ns: u64,
    pub sequence: u64,
    pub price: f64,
    pub size: f64,
    pub eligible: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayResult {
    pub instrument: u64,
    pub completed: Vec<Bar>,
    pub late: u64,
    pub ineligible: u64,
    pub checkpoint: BarBuilder,
    pub input_hash: String,
}
/// Bounded fixed worker groups; each instrument stays on one worker in source order.
/// This is market replay, not a claim of a complete strategy backtest.
pub fn replay(
    trades: &[ReplayTrade],
    interval_ns: u64,
    workers: usize,
) -> Result<Vec<ReplayResult>> {
    if workers == 0 || workers > 256 {
        return Err(Error::Invalid("worker count out of range".into()));
    }
    let mut groups: BTreeMap<u64, Vec<ReplayTrade>> = BTreeMap::new();
    for t in trades {
        if t.instrument == 0 {
            return Err(Error::Invalid("instrument missing".into()));
        }
        groups.entry(t.instrument).or_default().push(t.clone());
    }
    let mut lanes = vec![Vec::new(); workers.min(groups.len().max(1))];
    for (index, group) in groups.into_iter().enumerate() {
        let lane = index % lanes.len();
        lanes[lane].push(group);
    }
    std::thread::scope(|scope| {
        let handles: Vec<_> = lanes
            .into_iter()
            .map(|lane| {
                scope.spawn(move || -> Result<Vec<ReplayResult>> {
                    let mut results = vec![];
                    for (instrument, events) in lane {
                        let mut builder = BarBuilder::new(interval_ns)?;
                        let mut completed = vec![];
                        let mut late = 0;
                        let mut ineligible = 0;
                        for e in &events {
                            match builder.trade(e.sip_ns, e.price, e.size, e.eligible)? {
                                Update::Applied(Some(b)) => completed.push(b),
                                Update::Late => late += 1,
                                Update::Ineligible => ineligible += 1,
                                _ => {}
                            }
                        }
                        results.push(ReplayResult {
                            instrument,
                            completed,
                            late,
                            ineligible,
                            checkpoint: builder,
                            input_hash: content_hash(&events)?,
                        });
                    }
                    Ok(results)
                })
            })
            .collect();
        let mut result = vec![];
        for h in handles {
            result.extend(
                h.join()
                    .map_err(|_| Error::Unready("replay worker panicked".into()))??,
            );
        }
        result.sort_by_key(|r| r.instrument);
        Ok(result)
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worker_count_does_not_change_result() {
        let trades: Vec<_> = (1..=8)
            .flat_map(|instrument| {
                (1..=30).map(move |t| ReplayTrade {
                    instrument,
                    sip_ns: t,
                    sequence: t,
                    price: 10. + t as f64,
                    size: 1.,
                    eligible: true,
                })
            })
            .collect();
        assert_eq!(
            content_hash(&replay(&trades, 10, 1).unwrap()).unwrap(),
            content_hash(&replay(&trades, 10, 4).unwrap()).unwrap()
        );
    }
}
