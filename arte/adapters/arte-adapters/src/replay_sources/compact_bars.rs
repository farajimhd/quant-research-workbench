//! One-pass exact bar aggregation from a previously readback-verified REST source.
//! This prepares publication; it does not certify a provider's absolute completeness.
use super::Source as VerifiedSource;
use arte_core::{
    bar_catalogue::{Column, Coverage, Request, Source as BarSource, CONTRACT},
    content_hash,
    events::{EventKey, EventKind, Observation, Payload},
    trade_eligibility::Pinned,
    Error, Result,
};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub const CALCULATION: &str = "arte.eligible-trade-ohlc-bars.v1";

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Row {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub timeframe_ns: u64,
    pub bucket_start_ns: u64,
    pub source_generation: String,
    pub calculation_hash: String,
    pub price_scale: u8,
    pub size_scale: u8,
    pub open_atoms: i64,
    pub high_atoms: i64,
    pub low_atoms: i64,
    pub close_atoms: i64,
    pub volume_atoms: i64,
    pub notional_atoms: i128,
    pub trades: u64,
}

pub struct Prepared {
    pub(crate) coverage: Coverage,
    pub(crate) rows: Vec<Row>,
}
impl Prepared {
    pub fn coverage(&self) -> &Coverage {
        &self.coverage
    }
    pub fn row_count(&self) -> usize {
        self.rows.len()
    }
}

pub fn calculation_hash(
    policy: &Pinned,
    timeframe_ns: u64,
    price_scale: u8,
    size_scale: u8,
) -> Result<String> {
    if price_scale > 9 || size_scale > 9 || timeframe_ns == 0 {
        return Err(Error::Invalid(
            "bar calculation precision or timeframe".into(),
        ));
    }
    content_hash(&(
        CONTRACT,
        CALCULATION,
        policy.hash(),
        timeframe_ns,
        price_scale,
        size_scale,
    ))
}

struct Accum {
    start: u64,
    open: i64,
    high: i64,
    low: i64,
    close: i64,
    volume: i64,
    notional: i128,
    trades: u64,
}
impl Accum {
    fn new(start: u64, price: i64) -> Self {
        Self {
            start,
            open: price,
            high: price,
            low: price,
            close: price,
            volume: 0,
            notional: 0,
            trades: 0,
        }
    }
    fn apply(&mut self, price: i64, size: i64) -> Result<()> {
        self.high = self.high.max(price);
        self.low = self.low.min(price);
        self.close = price;
        self.volume = self
            .volume
            .checked_add(size)
            .ok_or_else(|| Error::Capacity("bar volume overflow".into()))?;
        self.notional = self
            .notional
            .checked_add(i128::from(price) * i128::from(size))
            .ok_or_else(|| Error::Capacity("bar notional overflow".into()))?;
        self.trades = self
            .trades
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("bar trade count overflow".into()))?;
        Ok(())
    }
    fn row(self, request: &Request, price_scale: u8, size_scale: u8) -> Row {
        Row {
            provider: request.provider,
            instrument: request.instruments[0],
            session: request.session,
            timeframe_ns: request.timeframe_ns,
            bucket_start_ns: self.start,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            price_scale,
            size_scale,
            open_atoms: self.open,
            high_atoms: self.high,
            low_atoms: self.low,
            close_atoms: self.close,
            volume_atoms: self.volume,
            notional_atoms: self.notional,
            trades: self.trades,
        }
    }
}

fn aggregate(
    observations: &[Observation],
    request: &Request,
    policy: &Pinned,
    price_scale: u8,
    size_scale: u8,
) -> Result<Vec<Row>> {
    request.validate()?;
    if request.instruments.len() != 1
        || request.columns
            != BTreeSet::from([
                Column::Open,
                Column::High,
                Column::Low,
                Column::Close,
                Column::Volume,
                Column::Notional,
                Column::Trades,
            ])
        || request.calculation_hash
            != calculation_hash(policy, request.timeframe_ns, price_scale, size_scale)?
    {
        return Err(Error::Conflict(
            "bar materialization configuration differs".into(),
        ));
    }
    policy.require_interval(request.interval, request.interval.start)?;
    if policy.provider() != request.provider {
        return Err(Error::Conflict("bar trade policy provider".into()));
    }
    let mut ordered: Vec<&Observation> = observations.iter().collect();
    ordered.sort_unstable_by_key(|e| (e.sip.ns, e.key.sequence));
    let mut seen = BTreeSet::<EventKey>::new();
    let mut rows = Vec::new();
    let mut current: Option<Accum> = None;
    for event in ordered {
        if event.key.provider != request.provider
            || event.key.instrument != request.instruments[0]
            || event.key.session != request.session
            || event.key.kind != EventKind::Trade
            || event.sip.ns < request.interval.start
            || event.sip.ns >= request.interval.end
            || !seen.insert(event.key.clone())
        {
            return Err(Error::Conflict("bar source scope or identity".into()));
        }
        if !policy.evaluate(event, event.sip.ns)? {
            continue;
        }
        let Payload::Trade { price, size, .. } = &event.payload else {
            return Err(Error::Conflict("bar source is not trade".into()));
        };
        let price = price.atoms_at_scale(price_scale)?;
        let size = size.atoms_at_scale(size_scale)?;
        if price <= 0 || size <= 0 {
            return Err(Error::Invalid("bar eligible trade price or size".into()));
        }
        let start = (event.sip.ns / request.timeframe_ns) * request.timeframe_ns;
        if current.as_ref().is_some_and(|c| c.start != start) {
            rows.push(
                current
                    .take()
                    .unwrap()
                    .row(request, price_scale, size_scale),
            );
        }
        let accumulator = current.get_or_insert_with(|| Accum::new(start, price));
        accumulator.apply(price, size)?;
    }
    if let Some(last) = current {
        rows.push(last.row(request, price_scale, size_scale));
    }
    if rows.len() > request.maximum_rows {
        return Err(Error::Capacity("bar sparse output rows".into()));
    }
    Ok(rows)
}

pub fn prepare(
    source: &VerifiedSource,
    request: Request,
    policy: &Pinned,
    price_scale: u8,
    size_scale: u8,
    source_as_of_ns: u64,
) -> Result<Prepared> {
    request.validate()?;
    let certificate = source.certificate();
    let id = certificate.id()?;
    if certificate.authority.kind != EventKind::Trade
        || certificate.authority.provider != request.provider
        || certificate.authority.instrument != request.instruments[0]
        || certificate.interval != request.interval
        || certificate.published_at_ns > source_as_of_ns
        || request.source_generation != id
    {
        return Err(Error::Conflict("bar source certificate differs".into()));
    }
    let rows = aggregate(
        source.observations(),
        &request,
        policy,
        price_scale,
        size_scale,
    )?;
    let coverage = Coverage {
        provider: request.provider,
        session: request.session,
        interval: request.interval,
        timeframe_ns: request.timeframe_ns,
        source_generation: request.source_generation.clone(),
        calculation_hash: request.calculation_hash.clone(),
        sources: BTreeMap::from([(
            request.instruments[0],
            BarSource {
                certificate_hash: id,
                price_scale,
                size_scale,
            },
        )]),
        published_at_ns: certificate.published_at_ns,
    };
    coverage.hash()?;
    Ok(Prepared { coverage, rows })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        coverage::Interval,
        events::{Decimal, EventKey, SourceTime},
    };
    fn policy() -> Pinned {
        let p = arte_core::trade_eligibility::Policy {
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
        let hash = p.hash().unwrap();
        Pinned::new(p, &hash).unwrap()
    }
    fn request(policy: &Pinned) -> Request {
        Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval: Interval {
                start: 1_000_000_000,
                end: 1_300_000_000,
            },
            timeframe_ns: 100_000_000,
            source_generation: "b".repeat(64),
            calculation_hash: calculation_hash(policy, 100_000_000, 2, 2).unwrap(),
            columns: [
                Column::Open,
                Column::High,
                Column::Low,
                Column::Close,
                Column::Volume,
                Column::Notional,
                Column::Trades,
            ]
            .into(),
            maximum_rows: 3,
        }
    }
    fn trade(sequence: u64, ns: u64, price: &str, size: &str) -> Observation {
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
                size: Decimal::parse(size).unwrap(),
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: ns,
            receipt: None,
        }
    }
    #[test]
    fn exact_aggregation_order_and_duplicate_rejection() {
        let p = policy();
        let r = request(&p);
        let trades = [
            trade(2, 1_120_000_000, "10.50", "1.25"),
            trade(1, 1_110_000_000, "10.25", "0.50"),
            trade(3, 1_210_000_000, "10.75", "2"),
        ];
        let rows = aggregate(&trades, &r, &p, 2, 2).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(
            (
                rows[0].open_atoms,
                rows[0].close_atoms,
                rows[0].volume_atoms,
                rows[0].trades
            ),
            (1025, 1050, 175, 2)
        );
        assert_eq!(rows[0].notional_atoms, 1025 * 50 + 1050 * 125);
        assert!(aggregate(&[trades[0].clone(), trades[0].clone()], &r, &p, 2, 2).is_err());
    }
}
