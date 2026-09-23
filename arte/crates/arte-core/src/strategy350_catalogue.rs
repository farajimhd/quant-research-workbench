//! Strategy 350 dependency selection. The catalogue must supply pinned
//! definitions; this module never imports the parent app or discovers defaults.
use crate::{
    coverage::{Dependency, Interval},
    dependency_plan::{self, Definition, Plan, Request},
    Error, Result,
};
use std::collections::BTreeSet;

pub const STRATEGY: &str = "arte.strategy-350.v1";
pub const SIGNAL: &str = "price-squeeze-early";
pub const WATCHLIST: &str = "early-squeeze-momentum-v24-tradability";
pub const LEVEL_BOOK: &str = "v7-historical-level-book";
pub const BASE_BAR_NS: u64 = 100_000_000;
const SECOND: u64 = 1_000_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WatchlistPolicy {
    NotRequired,
    Required,
}

#[derive(Clone, Copy)]
pub struct Ticker {
    pub instrument: u64,
    pub interval: Interval,
}

fn validate_tickers(tickers: &[Ticker]) -> Result<()> {
    if tickers.is_empty() || tickers.len() > 100_000 {
        return Err(Error::Invalid("Strategy 350 ticker population".into()));
    }
    let mut seen = BTreeSet::new();
    for ticker in tickers {
        ticker.interval.validate()?;
        if ticker.instrument == 0
            || !seen.insert(ticker.instrument)
            || !ticker.interval.start.is_multiple_of(BASE_BAR_NS)
            || !ticker.interval.end.is_multiple_of(BASE_BAR_NS)
        {
            return Err(Error::Invalid(
                "Strategy 350 ticker scope or bar grid".into(),
            ));
        }
    }
    Ok(())
}

fn requests(tickers: &[Ticker], dependencies: BTreeSet<Dependency>) -> Vec<Request> {
    tickers
        .iter()
        .flat_map(|ticker| {
            dependencies.iter().cloned().map(move |dependency| Request {
                strategy_instance: STRATEGY.into(),
                instrument: ticker.instrument,
                dependency,
                interval: ticker.interval,
            })
        })
        .collect()
}

pub fn screen_dependencies(watchlist_policy: WatchlistPolicy) -> BTreeSet<Dependency> {
    let mut result: BTreeSet<_> = [
        Dependency::Bars(BASE_BAR_NS),
        Dependency::MarketSignal(SIGNAL.into()),
        Dependency::Reference,
    ]
    .into();
    if watchlist_policy == WatchlistPolicy::Required {
        result.insert(Dependency::Watchlist(WATCHLIST.into()));
    }
    result
}

pub fn candidate_dependencies() -> BTreeSet<Dependency> {
    let mut result = BTreeSet::from([
        Dependency::Bars(BASE_BAR_NS),
        Dependency::PreviousClose,
        Dependency::Reference,
        Dependency::HistoricalSeed("v7".into()),
        Dependency::HistoricalLevelBook(LEVEL_BOOK.into()),
        Dependency::OfficialLuld,
        Dependency::Trades,
        Dependency::Quotes,
    ]);
    for seconds in [1, 2, 5, 10, 30] {
        result.insert(Dependency::Bars(seconds * SECOND));
    }
    for seconds in [1, 5, 10, 30] {
        result.insert(Dependency::Indicator(format!("forming_macd_{seconds}s")));
    }
    result.insert(Dependency::Indicator("execution_vwap".into()));
    result.insert(Dependency::Indicator("adaptive_stop_ranges".into()));
    result
}

/// Prepare only broad scanner products. No event/quote refinement is requested
/// for symbols that have not passed the first-stage discovery policy.
pub fn plan_screen(
    definitions: Vec<Definition>,
    tickers: &[Ticker],
    watchlist_policy: WatchlistPolicy,
    maximum_nodes: usize,
) -> Result<Plan> {
    validate_tickers(tickers)?;
    dependency_plan::build(
        definitions,
        &requests(tickers, screen_dependencies(watchlist_policy)),
        maximum_nodes,
    )
}

/// Called after the pinned scanner and rule set produce a candidate population.
/// Exact trade crossing and executable quote checks remain mandatory: a bar-only
/// approximation cannot silently satisfy these dependencies.
pub fn plan_candidates(
    definitions: Vec<Definition>,
    selected: &[Ticker],
    maximum_nodes: usize,
) -> Result<Option<Plan>> {
    if selected.is_empty() {
        return Ok(None);
    }
    validate_tickers(selected)?;
    dependency_plan::build(
        definitions,
        &requests(selected, candidate_dependencies()),
        maximum_nodes,
    )
    .map(Some)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution_interval::ExecutionInterval;
    use std::collections::BTreeMap;
    fn ticker() -> Ticker {
        Ticker {
            instrument: 10,
            interval: Interval {
                start: SECOND,
                end: 2 * SECOND,
            },
        }
    }
    fn definitions() -> Vec<Definition> {
        screen_dependencies(WatchlistPolicy::Required)
            .into_iter()
            .chain(candidate_dependencies())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(|dependency| Definition {
                execution_interval: match &dependency {
                    Dependency::Bars(_)
                    | Dependency::MarketSignal(_)
                    | Dependency::Watchlist(_) => ExecutionInterval::Fixed(BASE_BAR_NS),
                    _ => ExecutionInterval::Events,
                },
                dependency,
                implementation_hash: "a".repeat(64),
                inputs: vec![],
            })
            .collect()
    }
    #[test]
    fn scanner_stage_excludes_raw_refinement_and_candidate_stage_requires_it() {
        let definitions = definitions();
        let screen = plan_screen(
            definitions.clone(),
            &[ticker()],
            WatchlistPolicy::NotRequired,
            100,
        )
        .unwrap();
        let deps: BTreeSet<_> = screen
            .nodes
            .iter()
            .map(|n| n.key.dependency.clone())
            .collect();
        assert!(deps.contains(&Dependency::MarketSignal(SIGNAL.into())));
        assert!(!deps.contains(&Dependency::Watchlist(WATCHLIST.into())));
        let required = plan_screen(
            definitions.clone(),
            &[ticker()],
            WatchlistPolicy::Required,
            100,
        )
        .unwrap();
        assert!(required
            .nodes
            .iter()
            .any(|n| n.key.dependency == Dependency::Watchlist(WATCHLIST.into())));
        assert!(!deps.contains(&Dependency::Trades));
        assert!(!deps.contains(&Dependency::Quotes));
        let candidates = plan_candidates(definitions, &[ticker()], 100)
            .unwrap()
            .unwrap();
        let deps: BTreeSet<_> = candidates
            .nodes
            .iter()
            .map(|n| n.key.dependency.clone())
            .collect();
        assert!(deps.contains(&Dependency::Trades));
        assert!(deps.contains(&Dependency::Quotes));
        assert!(deps.contains(&Dependency::HistoricalLevelBook(LEVEL_BOOK.into())));
        assert!(deps.contains(&Dependency::Indicator("forming_macd_30s".into())));
        assert_eq!(plan_candidates(vec![], &[], 100).unwrap().is_none(), true);
    }
    #[test]
    fn missing_or_duplicate_ticker_authority_fails() {
        let defs = definitions();
        let mut missing: BTreeMap<_, _> = defs
            .into_iter()
            .map(|d| (d.dependency.clone(), d))
            .collect();
        missing.remove(&Dependency::Quotes);
        assert!(plan_candidates(missing.into_values().collect(), &[ticker()], 100).is_err());
        assert!(plan_screen(
            definitions(),
            &[ticker(), ticker()],
            WatchlistPolicy::NotRequired,
            100
        )
        .is_err());
    }
}
