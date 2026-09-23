//! Sparse MACD close schedule from one certified 100 ms historical product.
//! The caller applies these inputs at their end clock before evaluating later
//! events. No live receipt or historical trade execution timestamp is inferred.
use super::{exact_source, Config, Outcome, State};
use crate::{
    bar_catalogue::{Column, Complete, BASE_INTERVAL_NS},
    event_order::Scope,
    events::Decimal,
    Error, Result,
};

pub struct Projection {
    scope: Scope,
    session_start_ns: u64,
    session_end_ns: u64,
    config: Config,
    completed: Vec<exact_source::CompletedInput>,
}

/// The only decision-facing historical view. The final projected state is
/// deliberately not exposed: replay must advance this cursor monotonically.
pub struct Cursor {
    projection: Projection,
    state: State,
    next: usize,
    clock_ns: u64,
}
impl Projection {
    pub fn completed_count(&self) -> usize {
        self.completed.len()
    }
    pub fn cursor(self) -> Result<Cursor> {
        let state = State::new(
            self.scope,
            self.session_start_ns,
            self.session_end_ns,
            &self.config,
        )?;
        Ok(Cursor {
            clock_ns: self.session_start_ns,
            projection: self,
            state,
            next: 0,
        })
    }
}
impl Cursor {
    pub fn advance_to(&mut self, clock_ns: u64) -> Result<usize> {
        if clock_ns < self.clock_ns || clock_ns > self.projection.session_end_ns {
            return Err(Error::Conflict("historical MACD replay clock".into()));
        }
        if self
            .projection
            .completed
            .get(self.next)
            .is_none_or(|input| input.end_ns > clock_ns)
        {
            self.clock_ns = clock_ns;
            return Ok(0);
        }
        let mut staged = self.state.clone();
        let mut next = self.next;
        while let Some(input) = self.projection.completed.get(next) {
            if input.end_ns > clock_ns {
                break;
            }
            staged.observe_completed(input.timeframe_ns, input.end_ns, input.close)?;
            next += 1;
        }
        let applied = next - self.next;
        self.state = staged;
        self.next = next;
        self.clock_ns = clock_ns;
        Ok(applied)
    }
    pub fn preview_trade(
        &mut self,
        event_time_ns: u64,
        evaluated_at_ns: u64,
        price: Decimal,
    ) -> Result<Outcome> {
        if event_time_ns >= self.projection.session_end_ns
            || evaluated_at_ns < event_time_ns
            || !price.positive()
            || price.scale != self.projection.config.price_scale
        {
            return Err(Error::Invalid("historical MACD trade".into()));
        }
        self.advance_to(event_time_ns)?;
        self.state
            .preview_trade(event_time_ns, evaluated_at_ns, price)
    }
}

pub fn project(
    product: &Complete,
    expected_request_hash: &str,
    expected_coverage_hash: &str,
    config: &Config,
) -> Result<Projection> {
    let request = product.request();
    config.hash()?;
    if request.hash()? != expected_request_hash
        || product.coverage_hash() != expected_coverage_hash
        || request.instruments.len() != 1
        || request.timeframe_ns != BASE_INTERVAL_NS
        || request.calculation_hash != config.source_algorithm_hash
        || !request.columns.contains(&Column::Close)
        || !request.columns.contains(&Column::Trades)
    {
        return Err(Error::Invalid(
            "Strategy 350 historical MACD product".into(),
        ));
    }
    let first = product
        .batches()
        .first()
        .ok_or_else(|| Error::Unready("historical MACD bars missing".into()))?;
    let scope = Scope {
        provider: request.provider,
        instrument: request.instruments[0],
        session: request.session,
    };
    if first.price_scale != config.price_scale {
        return Err(Error::Conflict("historical MACD price scale".into()));
    }
    let mut source = exact_source::Source::new(
        scope,
        request.interval.start,
        request.interval.end,
        config.price_scale,
        request.calculation_hash.clone(),
    )?;
    let mut state = State::new(scope, request.interval.start, request.interval.end, config)?;
    let mut completed = Vec::new();
    for batch in product.batches() {
        if batch.instrument != scope.instrument || batch.price_scale != config.price_scale {
            return Err(Error::Conflict("historical MACD source changed".into()));
        }
        let closes = batch
            .close
            .as_deref()
            .ok_or_else(|| Error::Unready("historical MACD close missing".into()))?;
        let trades = batch
            .trades
            .as_deref()
            .ok_or_else(|| Error::Unready("historical MACD trades missing".into()))?;
        for slot in 0..batch.count as usize {
            let start = (slot as u64)
                .checked_mul(BASE_INTERVAL_NS)
                .and_then(|offset| batch.first_start_ns.checked_add(offset))
                .ok_or_else(|| Error::Capacity("historical MACD clock".into()))?;
            let end = start
                .checked_add(BASE_INTERVAL_NS)
                .ok_or_else(|| Error::Capacity("historical MACD end".into()))?;
            let bar = if batch.present[slot] {
                if trades[slot] == 0 {
                    return Err(Error::Conflict("historical MACD empty trade bar".into()));
                }
                Some((start, closes[slot]))
            } else {
                None
            };
            for input in source.advance_compact_close(bar, end)? {
                state.observe_completed(input.timeframe_ns, input.end_ns, input.close)?;
                completed.push(input);
            }
        }
    }
    if source.watermark_ns() != request.interval.end {
        return Err(Error::Conflict("historical MACD coverage".into()));
    }
    state.require_source(&source)?;
    Ok(Projection {
        scope,
        session_start_ns: request.interval.start,
        session_end_ns: request.interval.end,
        config: config.clone(),
        completed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bar_catalogue::{Batch, Coverage, Readback, Request, Source},
        coverage::Interval,
        execution_interval::ExecutionInterval,
    };
    const SECOND: u64 = 1_000_000_000;
    const START: u64 = 30 * SECOND;

    fn product() -> Complete {
        let request = Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval: Interval {
                start: START,
                end: START + 30 * SECOND,
            },
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: [Column::Close, Column::Trades].into(),
            maximum_rows: 300,
        };
        let coverage = Coverage {
            provider: request.provider,
            session: request.session,
            interval: request.interval,
            timeframe_ns: request.timeframe_ns,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: [(
                10,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 0,
                },
            )]
            .into(),
            published_at_ns: START + 31 * SECOND,
        };
        let mut present = vec![false; 300];
        let mut close = vec![0; 300];
        let mut trades = vec![0; 300];
        for (slot, price) in [(0, 1000), (9, 1010), (299, 1050)] {
            present[slot] = true;
            close[slot] = price;
            trades[slot] = 1;
        }
        let batch = Batch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument: 10,
            first_start_ns: START,
            count: 300,
            price_scale: 2,
            size_scale: 0,
            present,
            open: None,
            high: None,
            low: None,
            close: Some(close),
            volume: None,
            notional: None,
            trades: Some(trades),
        };
        let mut readback = Readback::new(request, &coverage, START + 31 * SECOND).unwrap();
        readback.observe(batch).unwrap();
        readback.finish().unwrap()
    }

    #[test]
    fn certified_sparse_compact_bars_seal_without_fabricated_trades() {
        let complete = product();
        let config = Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            source_algorithm_hash: "b".repeat(64),
        };
        let request_hash = complete.request().hash().unwrap();
        let coverage_hash = complete.coverage_hash().to_owned();
        let projection = project(&complete, &request_hash, &coverage_hash, &config).unwrap();
        assert_eq!(projection.completed_count(), 7);
        assert_eq!(
            projection
                .completed
                .iter()
                .filter(|input| input.timeframe_ns == SECOND)
                .count(),
            2
        );
        assert_eq!(
            projection
                .completed
                .iter()
                .filter(|input| input.timeframe_ns == 30 * SECOND)
                .count(),
            1
        );
        assert_eq!(
            projection.completed.last().unwrap().end_ns,
            START + 30 * SECOND
        );
        let mut cursor = projection.cursor().unwrap();
        let price = Decimal {
            atoms: 1000,
            scale: 2,
        };
        assert!(
            !cursor
                .preview_trade(START + SECOND - 1, START + SECOND - 1, price)
                .unwrap()
                .bullish
        );
        assert_eq!(cursor.advance_to(START + SECOND).unwrap(), 1);
        assert!(cursor.advance_to(START + SECOND - 1).is_err());
        let before_later_closes = cursor
            .preview_trade(START + 4 * SECOND, START + 4 * SECOND, price)
            .unwrap();
        assert!(before_later_closes.previews[0].is_none());
        assert!(before_later_closes.previews[2].is_none());
        assert_eq!(cursor.advance_to(START + 29 * SECOND).unwrap(), 2);
        assert_eq!(cursor.advance_to(START + 30 * SECOND).unwrap(), 4);
        assert!(project(&complete, &"f".repeat(64), &coverage_hash, &config).is_err());
        assert!(project(&complete, &request_hash, &"f".repeat(64), &config).is_err());
    }
}
