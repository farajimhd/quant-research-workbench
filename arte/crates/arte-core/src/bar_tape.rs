//! Deterministic sparse decision traversal over pinned dense calculation bars.
use crate::{
    bar_catalogue::{Batch, Complete},
    Error, Result,
};
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BinaryHeap},
};

#[derive(Clone, Copy)]
struct Cursor {
    batch: usize,
    slot: usize,
}
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Head {
    end_ns: u64,
    instrument: u64,
    session: u32,
    product: usize,
}

pub struct View<'a> {
    pub instrument: u64,
    pub session: u32,
    pub start_ns: u64,
    pub end_ns: u64,
    pub batch: &'a Batch,
    pub slot: usize,
}
pub struct Tape {
    products: Vec<Complete>,
    cursors: Vec<Cursor>,
    ready: BinaryHeap<Reverse<Head>>,
    last_end_ns: Option<u64>,
}
impl Tape {
    pub fn new(products: Vec<Complete>) -> Result<Self> {
        if products.is_empty() || products.len() > 100_000 {
            return Err(Error::Invalid("bar tape product count".into()));
        }
        let mut scope = BTreeMap::<u64, Vec<(u64, u64)>>::new();
        for product in &products {
            let request = product.request();
            if request.timeframe_ns != crate::bar_catalogue::BASE_INTERVAL_NS {
                return Err(Error::Conflict("bar tape requires 100 ms base".into()));
            }
            for &instrument in &request.instruments {
                scope
                    .entry(instrument)
                    .or_default()
                    .push((request.interval.start, request.interval.end));
            }
        }
        for spans in scope.values_mut() {
            spans.sort_unstable();
            if spans.windows(2).any(|w| w[0].1 > w[1].0) {
                return Err(Error::Conflict("bar tape overlapping generations".into()));
            }
        }
        let count = products.len();
        let mut tape = Self {
            products,
            cursors: vec![Cursor { batch: 0, slot: 0 }; count],
            ready: BinaryHeap::new(),
            last_end_ns: None,
        };
        for product in 0..count {
            tape.push_next(product)?;
        }
        Ok(tape)
    }
    fn push_next(&mut self, product: usize) -> Result<()> {
        let request = self.products[product].request();
        let batches = self.products[product].batches();
        let cursor = &mut self.cursors[product];
        while cursor.batch < batches.len() {
            let batch = &batches[cursor.batch];
            while cursor.slot < batch.count as usize {
                let slot = cursor.slot;
                cursor.slot += 1;
                if batch.present[slot] {
                    let end_ns = batch
                        .first_start_ns
                        .checked_add((slot as u64 + 1) * request.timeframe_ns)
                        .ok_or_else(|| Error::Capacity("bar tape clock".into()))?;
                    self.ready.push(Reverse(Head {
                        end_ns,
                        instrument: batch.instrument,
                        session: request.session,
                        product,
                    }));
                    return Ok(());
                }
            }
            cursor.batch += 1;
            cursor.slot = 0;
        }
        Ok(())
    }
    /// Market time and instrument break ties. The returned view borrows the
    /// original columnar batch; callers must finish it before advancing again.
    pub fn next_present(&mut self) -> Result<Option<View<'_>>> {
        let Some(Reverse(head)) = self.ready.pop() else {
            return Ok(None);
        };
        if self.last_end_ns.is_some_and(|last| head.end_ns < last) {
            return Err(Error::Conflict("bar tape time regressed".into()));
        }
        self.last_end_ns = Some(head.end_ns);
        let cursor = self.cursors[head.product];
        let batch_index = cursor.batch;
        let slot = cursor
            .slot
            .checked_sub(1)
            .ok_or_else(|| Error::Conflict("bar tape cursor missing".into()))?;
        self.push_next(head.product)?;
        let batch = &self.products[head.product].batches()[batch_index];
        let start_ns = head.end_ns - self.products[head.product].request().timeframe_ns;
        Ok(Some(View {
            instrument: head.instrument,
            session: head.session,
            start_ns,
            end_ns: head.end_ns,
            batch,
            slot,
        }))
    }
    pub fn is_done(&self) -> bool {
        self.ready.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bar_catalogue::{Column, Coverage, Readback, Request, Source},
        coverage::Interval,
    };
    use std::collections::{BTreeMap, BTreeSet};
    fn product(instrument: u64, present: Vec<bool>) -> Complete {
        let interval = Interval {
            start: 1_000_000_000,
            end: 1_300_000_000,
        };
        let request = Request {
            provider: 1,
            instruments: vec![instrument],
            session: 20260922,
            interval,
            timeframe_ns: 100_000_000,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Close]),
            maximum_rows: 3,
        };
        let coverage = Coverage {
            provider: 1,
            session: request.session,
            interval,
            timeframe_ns: request.timeframe_ns,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                instrument,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 2,
                },
            )]),
            published_at_ns: 2_000_000_000,
        };
        let mut readback = Readback::new(request.clone(), &coverage, 2_000_000_000).unwrap();
        let values = present
            .iter()
            .map(|active| if *active { 1000 } else { 0 })
            .collect();
        readback
            .observe(Batch {
                request_hash: request.hash().unwrap(),
                coverage_hash: coverage.hash().unwrap(),
                instrument,
                first_start_ns: interval.start,
                count: 3,
                price_scale: 2,
                size_scale: 2,
                present,
                open: None,
                high: None,
                low: None,
                close: Some(values),
                volume: None,
                notional: None,
                trades: None,
            })
            .unwrap();
        readback.finish().unwrap()
    }
    #[test]
    fn stable_cross_ticker_order_skips_empty_buckets() {
        let mut tape = Tape::new(vec![
            product(20, vec![false, true, true]),
            product(10, vec![true, true, false]),
        ])
        .unwrap();
        let mut seen = Vec::new();
        while let Some(view) = tape.next_present().unwrap() {
            seen.push((
                view.end_ns,
                view.instrument,
                view.batch.close.as_ref().unwrap()[view.slot],
            ));
        }
        assert_eq!(
            seen,
            vec![
                (1_100_000_000, 10, 1000),
                (1_200_000_000, 10, 1000),
                (1_200_000_000, 20, 1000),
                (1_300_000_000, 20, 1000)
            ]
        );
        assert!(tape.is_done());
        assert!(Tape::new(vec![
            product(10, vec![true, false, false]),
            product(10, vec![false, true, false])
        ])
        .is_err());
    }
}
