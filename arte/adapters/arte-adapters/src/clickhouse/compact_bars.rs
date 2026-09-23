//! Read sparse exact bars in bounded ranges; expand empty certified buckets in RAM.
use super::*;
use crate::replay_sources::compact_bars::{Prepared, Row};
use arte_core::bar_catalogue::{Batch, Column, Complete, Coverage, Readback, Request};
use arte_core::config::Acceptance;
use std::collections::{BTreeMap, BTreeSet};

const BARS: &str = "compact_bars_v1";
const COVERAGE: &str = "compact_bar_coverage_v1";
const PAGE_BUCKETS: u64 = 1_000;

#[derive(Clone, Copy)]
struct Page {
    instrument: u64,
    start: u64,
    count: u32,
    price_scale: u8,
    size_scale: u8,
}

fn number<T: std::str::FromStr>(row: &Value, name: &str) -> Result<T> {
    let value = row
        .get(name)
        .ok_or_else(|| Error::Invalid(format!("compact bar {name} missing")))?;
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
        .parse::<T>()
        .map_err(|_| Error::Invalid(format!("compact bar {name} malformed")))
}
fn string(row: &Value, name: &str) -> Result<String> {
    row.get(name)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| Error::Invalid(format!("compact bar {name} missing")))
}
fn decode_sparse(line: &str) -> Result<Row> {
    let value: Value = serde_json::from_str(line)
        .map_err(|_| Error::Invalid("compact bar sparse row JSON".into()))?;
    Ok(Row {
        provider: number(&value, "provider")?,
        instrument: number(&value, "instrument")?,
        session: number(&value, "session")?,
        timeframe_ns: number(&value, "timeframe_ns")?,
        bucket_start_ns: number(&value, "bucket_start_ns")?,
        source_generation: string(&value, "source_generation")?,
        calculation_hash: string(&value, "calculation_hash")?,
        price_scale: number(&value, "price_scale")?,
        size_scale: number(&value, "size_scale")?,
        open_atoms: number(&value, "open_atoms")?,
        high_atoms: number(&value, "high_atoms")?,
        low_atoms: number(&value, "low_atoms")?,
        close_atoms: number(&value, "close_atoms")?,
        volume_atoms: number(&value, "volume_atoms")?,
        notional_atoms: number(&value, "notional_atoms")?,
        trades: number(&value, "trades")?,
    })
}
fn publication_request(coverage: &Coverage) -> Result<Request> {
    let instrument = *coverage
        .sources
        .keys()
        .next()
        .ok_or_else(|| Error::Invalid("bar publication source missing".into()))?;
    let buckets = (coverage.interval.end - coverage.interval.start) / coverage.timeframe_ns;
    let maximum_rows = usize::try_from(buckets)
        .map_err(|_| Error::Capacity("bar publication bucket count".into()))?;
    let request = Request {
        provider: coverage.provider,
        instruments: vec![instrument],
        session: coverage.session,
        interval: coverage.interval,
        timeframe_ns: coverage.timeframe_ns,
        source_generation: coverage.source_generation.clone(),
        calculation_hash: coverage.calculation_hash.clone(),
        columns: BTreeSet::from([
            Column::Open,
            Column::High,
            Column::Low,
            Column::Close,
            Column::Volume,
            Column::Notional,
            Column::Trades,
        ]),
        maximum_rows,
    };
    request.validate()?;
    Ok(request)
}
fn verify_prepared(prepared: &Prepared) -> Result<Request> {
    prepared.coverage.hash()?;
    if prepared.coverage.sources.len() != 1 {
        return Err(Error::Invalid("bar publication ticker count".into()));
    }
    let request = publication_request(&prepared.coverage)?;
    let source = prepared
        .coverage
        .sources
        .get(&request.instruments[0])
        .unwrap();
    let mut prior = None;
    for row in &prepared.rows {
        if row.provider != request.provider
            || row.instrument != request.instruments[0]
            || row.session != request.session
            || row.timeframe_ns != request.timeframe_ns
            || row.source_generation != request.source_generation
            || row.calculation_hash != request.calculation_hash
            || row.price_scale != source.price_scale
            || row.size_scale != source.size_scale
            || row.bucket_start_ns < request.interval.start
            || row.bucket_start_ns >= request.interval.end
            || !row.bucket_start_ns.is_multiple_of(request.timeframe_ns)
            || prior.is_some_and(|at| row.bucket_start_ns <= at)
            || row.open_atoms <= 0
            || row.high_atoms < row.open_atoms.max(row.close_atoms)
            || row.low_atoms <= 0
            || row.low_atoms > row.open_atoms.min(row.close_atoms)
            || row.volume_atoms <= 0
            || row.notional_atoms <= 0
            || row.trades == 0
        {
            return Err(Error::Conflict(
                "bar publication row geometry or identity".into(),
            ));
        }
        prior = Some(row.bucket_start_ns);
    }
    if prepared.rows.len() > request.maximum_rows {
        return Err(Error::Capacity("bar publication row count".into()));
    }
    Ok(request)
}
pub fn compact_bar_publication_scope(coverage: &Coverage) -> Result<String> {
    coverage.hash()?;
    arte_core::content_hash(&(
        "arte.compact-bar-publication.v1",
        coverage.provider,
        coverage.session,
        coverage.interval,
        &coverage.source_generation,
        &coverage.calculation_hash,
        coverage.sources.keys().collect::<Vec<_>>(),
    ))
}
fn column_name(column: Column) -> &'static str {
    match column {
        Column::Open => "open_atoms",
        Column::High => "high_atoms",
        Column::Low => "low_atoms",
        Column::Close => "close_atoms",
        Column::Volume => "volume_atoms",
        Column::Notional => "notional_atoms",
        Column::Trades => "trades",
    }
}
fn query(
    database: &str,
    request: &Request,
    instrument: u64,
    start: u64,
    end: u64,
) -> Result<String> {
    identifier(database)?;
    request.validate()?;
    if request.instruments.binary_search(&instrument).is_err()
        || start < request.interval.start
        || end > request.interval.end
        || start >= end
        || !start.is_multiple_of(request.timeframe_ns)
        || !end.is_multiple_of(request.timeframe_ns)
        || (end - start) / request.timeframe_ns > PAGE_BUCKETS
    {
        return Err(Error::Invalid("compact bar page".into()));
    }
    let mut columns = String::from("provider,instrument,session,timeframe_ns,bucket_start_ns,source_generation,calculation_hash,price_scale,size_scale");
    for column in &request.columns {
        columns.push(',');
        columns.push_str(column_name(*column));
    }
    Ok(format!("SELECT {columns} FROM {database}.{BARS} WHERE provider={} AND instrument={instrument} AND session={} AND timeframe_ns={} AND source_generation='{}' AND calculation_hash='{}' AND bucket_start_ns >= {start} AND bucket_start_ns < {end} ORDER BY bucket_start_ns LIMIT {} FORMAT JSONEachRow",
        request.provider, request.session, request.timeframe_ns,
        request.source_generation, request.calculation_hash, PAGE_BUCKETS+1))
}
fn decode_page(body: &str, request: &Request, page: Page, coverage_hash: &str) -> Result<Batch> {
    let Page {
        instrument,
        start,
        count,
        price_scale,
        size_scale,
    } = page;
    let n = count as usize;
    let selected = |c| request.columns.contains(&c);
    let mut batch = Batch {
        request_hash: request.hash()?,
        coverage_hash: coverage_hash.into(),
        instrument,
        first_start_ns: start,
        count,
        price_scale,
        size_scale,
        present: vec![false; n],
        open: selected(Column::Open).then(|| vec![0; n]),
        high: selected(Column::High).then(|| vec![0; n]),
        low: selected(Column::Low).then(|| vec![0; n]),
        close: selected(Column::Close).then(|| vec![0; n]),
        volume: selected(Column::Volume).then(|| vec![0; n]),
        notional: selected(Column::Notional).then(|| vec![0; n]),
        trades: selected(Column::Trades).then(|| vec![0; n]),
    };
    let mut rows = 0usize;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        rows += 1;
        if rows > n {
            return Err(Error::Capacity("compact bar page rows".into()));
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("compact bar row JSON".into()))?;
        let at: u64 = number(&row, "bucket_start_ns")?;
        let next = start
            .checked_add(count as u64 * request.timeframe_ns)
            .ok_or_else(|| Error::Capacity("compact bar page clock".into()))?;
        if number::<u16>(&row, "provider")? != request.provider
            || number::<u64>(&row, "instrument")? != instrument
            || number::<u32>(&row, "session")? != request.session
            || number::<u64>(&row, "timeframe_ns")? != request.timeframe_ns
            || row.get("source_generation").and_then(Value::as_str)
                != Some(request.source_generation.as_str())
            || row.get("calculation_hash").and_then(Value::as_str)
                != Some(request.calculation_hash.as_str())
            || at < start
            || at >= next
            || !(at - start).is_multiple_of(request.timeframe_ns)
        {
            return Err(Error::Conflict(
                "compact bar row escaped page identity".into(),
            ));
        }
        let i = ((at - start) / request.timeframe_ns) as usize;
        if batch.present[i] {
            return Err(Error::Conflict("duplicate compact bar bucket".into()));
        }
        batch.present[i] = true;
        let this_scale = (
            number::<u8>(&row, "price_scale")?,
            number::<u8>(&row, "size_scale")?,
        );
        if this_scale != (price_scale, size_scale) {
            return Err(Error::Conflict(
                "compact bar row scale differs from coverage".into(),
            ));
        }
        macro_rules! set {
            ($field:ident, $name:literal, $type:ty) => {
                if let Some(values) = &mut batch.$field {
                    values[i] = number::<$type>(&row, $name)?;
                }
            };
        }
        set!(open, "open_atoms", i64);
        set!(high, "high_atoms", i64);
        set!(low, "low_atoms", i64);
        set!(close, "close_atoms", i64);
        set!(volume, "volume_atoms", i64);
        set!(notional, "notional_atoms", i128);
        set!(trades, "trades", u64);
    }
    batch.validate(request)?;
    Ok(batch)
}

impl ClickHouse {
    async fn read_sparse_page(&self, request: &Request, start: u64, end: u64) -> Result<Vec<Row>> {
        let sql = query(&self.database, request, request.instruments[0], start, end)?;
        let body = self.request(&sql, String::new()).await?;
        let mut rows = Vec::new();
        let mut previous = None;
        for line in body.lines().filter(|line| !line.trim().is_empty()) {
            if rows.len() >= PAGE_BUCKETS as usize {
                return Err(Error::Capacity(
                    "compact bar sparse page exceeds bound".into(),
                ));
            }
            let row = decode_sparse(line)?;
            if row.provider != request.provider
                || row.instrument != request.instruments[0]
                || row.session != request.session
                || row.timeframe_ns != request.timeframe_ns
                || row.source_generation != request.source_generation
                || row.calculation_hash != request.calculation_hash
                || row.bucket_start_ns < start
                || row.bucket_start_ns >= end
                || previous.is_some_and(|at| row.bucket_start_ns <= at)
            {
                return Err(Error::Conflict(
                    "compact bar sparse page order or identity".into(),
                ));
            }
            previous = Some(row.bucket_start_ns);
            rows.push(row);
        }
        Ok(rows)
    }
    /// Publish only with a held maintenance lease and explicit extraction,
    /// source, storage and durability acceptance. Every sparse page is compared
    /// to exact readback before its coverage manifest becomes visible.
    pub async fn publish_compact_bars(
        &self,
        prepared: &Prepared,
        now_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [
            Acceptance::RepositoryExtracted,
            Acceptance::SourceIdentity,
            Acceptance::EventStorage,
            Acceptance::Durability,
        ] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "compact bar publication acceptance missing: {required:?}"
                )));
            }
        }
        let request = verify_prepared(prepared)?;
        lease.require(&compact_bar_publication_scope(&prepared.coverage)?)?;
        let source = prepared
            .coverage
            .sources
            .get(&request.instruments[0])
            .unwrap();
        let verified = self.load_acquisition(&source.certificate_hash).await?;
        let certificate = verified.certificate();
        if certificate.id()? != source.certificate_hash
            || request.source_generation != source.certificate_hash
            || certificate.authority.provider != request.provider
            || certificate.authority.instrument != request.instruments[0]
            || certificate.authority.kind != arte_core::events::EventKind::Trade
            || certificate.interval != request.interval
            || certificate.published_at_ns > now_ns
        {
            return Err(Error::Conflict(
                "compact bar source certificate differs".into(),
            ));
        }
        self.verify_storage(BARS).await?;
        self.verify_storage(COVERAGE).await?;
        let mut start = request.interval.start;
        let mut first = 0usize;
        while start < request.interval.end {
            let end = start
                .saturating_add(PAGE_BUCKETS.saturating_mul(request.timeframe_ns))
                .min(request.interval.end);
            let next =
                first + prepared.rows[first..].partition_point(|row| row.bucket_start_ns < end);
            let expected = &prepared.rows[first..next];
            let existing = self.read_sparse_page(&request, start, end).await?;
            let mut by_start = BTreeMap::new();
            for row in existing {
                if by_start.insert(row.bucket_start_ns, row).is_some() {
                    return Err(Error::Conflict("duplicate compact bar persisted".into()));
                }
            }
            for row in by_start.values() {
                if expected
                    .binary_search_by_key(&row.bucket_start_ns, |r| r.bucket_start_ns)
                    .is_err()
                {
                    return Err(Error::Conflict("unexpected compact bar persisted".into()));
                }
            }
            let mut missing = Vec::new();
            for row in expected {
                match by_start.get(&row.bucket_start_ns) {
                    Some(existing) if existing == row => {}
                    Some(_) => {
                        return Err(Error::Conflict("compact bar immutable row differs".into()))
                    }
                    None => missing.push(
                        serde_json::to_value(row)
                            .map_err(|e| Error::Serialization(e.to_string()))?,
                    ),
                }
            }
            if !missing.is_empty() {
                self.insert(BARS, &missing).await?;
            }
            if self.read_sparse_page(&request, start, end).await? != expected {
                return Err(Error::Conflict(
                    "compact bar sparse page readback differs".into(),
                ));
            }
            first = next;
            start = end;
        }
        if first != prepared.rows.len() {
            return Err(Error::Conflict("compact bar unpublished suffix".into()));
        }
        let mut coverage = prepared.coverage.clone();
        coverage.published_at_ns = now_ns;
        let hash = coverage.hash()?;
        let payload =
            serde_json::to_string(&coverage).map_err(|e| Error::Serialization(e.to_string()))?;
        if let Some(existing) = self
            .immutable_value(COVERAGE, "coverage_hash", &hash, "payload_json")
            .await?
        {
            if existing != payload {
                return Err(Error::Conflict(
                    "compact bar coverage already differs".into(),
                ));
            }
        } else {
            self.insert(
                COVERAGE,
                &[serde_json::json!({"coverage_hash":hash,"payload_json":payload})],
            )
            .await?;
        }
        if self
            .immutable_value(COVERAGE, "coverage_hash", &hash, "payload_json")
            .await?
            != Some(payload)
        {
            return Err(Error::Conflict(
                "compact bar coverage readback differs".into(),
            ));
        }
        Ok(hash)
    }
    /// Caller pins the coverage hash obtained from the independently published
    /// catalogue. This read does not create or certify a missing source range.
    pub async fn read_compact_bars(
        &self,
        request: Request,
        expected_coverage_hash: &str,
        source_as_of_ns: u64,
    ) -> Result<Complete> {
        request.validate()?;
        if expected_coverage_hash.len() != 64
            || !expected_coverage_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("compact bar expected coverage hash".into()));
        }
        self.verify_storage(BARS).await?;
        self.verify_storage(COVERAGE).await?;
        let sql=format!("SELECT DISTINCT payload_json FROM {}.{COVERAGE} WHERE coverage_hash='{expected_coverage_hash}' LIMIT 2 FORMAT JSONEachRow",self.database);
        let body = self.request(&sql, String::new()).await?;
        let rows: Vec<Value> = body
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str(line)
                    .map_err(|_| Error::Invalid("compact bar coverage JSON".into()))
            })
            .collect::<Result<_>>()?;
        if rows.len() != 1 {
            return Err(Error::Unready(
                "compact bar coverage absent or conflicting".into(),
            ));
        }
        let payload = rows[0]
            .get("payload_json")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("compact bar coverage payload missing".into()))?;
        let coverage: Coverage = serde_json::from_str(payload)
            .map_err(|_| Error::Invalid("compact bar coverage payload malformed".into()))?;
        if coverage.hash()? != expected_coverage_hash {
            return Err(Error::Conflict(
                "compact bar coverage content differs".into(),
            ));
        }
        let mut verified = Readback::new(request.clone(), &coverage, source_as_of_ns)?;
        for &instrument in &request.instruments {
            let source = coverage
                .sources
                .get(&instrument)
                .ok_or_else(|| Error::Unready("compact bar precision missing".into()))?;
            let mut start = request.interval.start;
            while start < request.interval.end {
                let end = start
                    .saturating_add(PAGE_BUCKETS.saturating_mul(request.timeframe_ns))
                    .min(request.interval.end);
                let sql = query(&self.database, &request, instrument, start, end)?;
                let page = self.request(&sql, String::new()).await?;
                let count = ((end - start) / request.timeframe_ns) as u32;
                verified.observe(decode_page(
                    &page,
                    &request,
                    Page {
                        instrument,
                        start,
                        count,
                        price_scale: source.price_scale,
                        size_scale: source.size_scale,
                    },
                    expected_coverage_hash,
                )?)?;
                start = end;
            }
        }
        verified.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::bar_catalogue::Source as BarSource;
    use arte_core::coverage::Interval;
    use std::collections::BTreeSet;
    fn request() -> Request {
        Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval: Interval {
                start: 1_000_000_000,
                end: 1_300_000_000,
            },
            timeframe_ns: 100_000_000,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Close, Column::Trades]),
            maximum_rows: 3,
        }
    }
    #[test]
    fn projected_query_and_sparse_expansion() {
        let r = request();
        let sql = query("arte", &r, 10, r.interval.start, r.interval.end).unwrap();
        assert!(sql.contains("close_atoms,trades FROM arte.compact_bars_v1"));
        assert!(!sql.contains("open_atoms"));
        let body=format!("{{\"provider\":1,\"instrument\":10,\"session\":20260922,\"timeframe_ns\":100000000,\"bucket_start_ns\":1100000000,\"source_generation\":\"{}\",\"calculation_hash\":\"{}\",\"price_scale\":2,\"size_scale\":2,\"close_atoms\":1050,\"trades\":2}}",r.source_generation,r.calculation_hash);
        let page = Page {
            instrument: 10,
            start: r.interval.start,
            count: 3,
            price_scale: 2,
            size_scale: 2,
        };
        let batch = decode_page(&body, &r, page, &"c".repeat(64)).unwrap();
        assert_eq!(batch.present, vec![false, true, false]);
        assert_eq!(batch.close.unwrap(), vec![0, 1050, 0]);
        assert_eq!(batch.trades.unwrap(), vec![0, 2, 0]);
        let empty = decode_page("", &r, page, &"c".repeat(64)).unwrap();
        assert_eq!((empty.price_scale, empty.size_scale), (2, 2));
        assert_eq!(empty.present, vec![false; 3]);
        assert!(decode_page(&(body.clone() + "\n" + &body), &r, page, &"c".repeat(64)).is_err());
    }
    #[test]
    fn publisher_rejects_changed_or_unordered_rows_before_io() {
        let r = request();
        let coverage = Coverage {
            provider: r.provider,
            session: r.session,
            interval: r.interval,
            timeframe_ns: r.timeframe_ns,
            source_generation: r.source_generation.clone(),
            calculation_hash: r.calculation_hash.clone(),
            sources: BTreeMap::from([(
                10,
                BarSource {
                    certificate_hash: r.source_generation.clone(),
                    price_scale: 2,
                    size_scale: 2,
                },
            )]),
            published_at_ns: 2_000_000_000,
        };
        let row = Row {
            provider: 1,
            instrument: 10,
            session: r.session,
            timeframe_ns: r.timeframe_ns,
            bucket_start_ns: r.interval.start,
            source_generation: r.source_generation.clone(),
            calculation_hash: r.calculation_hash.clone(),
            price_scale: 2,
            size_scale: 2,
            open_atoms: 1000,
            high_atoms: 1100,
            low_atoms: 900,
            close_atoms: 1050,
            volume_atoms: 100,
            notional_atoms: 102500,
            trades: 1,
        };
        let mut prepared = Prepared {
            coverage,
            rows: vec![row.clone()],
        };
        assert!(verify_prepared(&prepared).is_ok());
        let scope = compact_bar_publication_scope(&prepared.coverage).unwrap();
        prepared.coverage.published_at_ns += 1;
        assert_eq!(
            scope,
            compact_bar_publication_scope(&prepared.coverage).unwrap()
        );
        prepared.rows[0].low_atoms = 1060;
        assert!(verify_prepared(&prepared).is_err());
        prepared.rows[0] = row.clone();
        prepared.rows.push(row);
        assert!(verify_prepared(&prepared).is_err());
    }
}
