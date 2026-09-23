//! Read sparse Boolean transitions only under pinned complete coverage.
use super::*;
use arte_core::boolean_catalogue::{
    Batch, Complete, Coverage, Readback, Request, TransitionDigest,
};
pub use arte_core::boolean_compute::DenseBatch;
use arte_core::execution_interval::ExecutionInterval;
use arte_core::{bar_catalogue::Complete as BarComplete, config::Acceptance};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const TRANSITIONS: &str = "boolean_transitions_v1";
const COVERAGE: &str = "boolean_coverage_v1";
const PAGE_BUCKETS: u64 = arte_core::boolean_compute::MAX_BATCH_ROWS as u64;
const BASE: u64 = arte_core::bar_catalogue::BASE_INTERVAL_NS;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct TransitionRow {
    request_hash: String,
    provider: u16,
    instrument: u64,
    session: u32,
    bucket_start_ns: u64,
    known: u8,
    value: u8,
}
pub struct Prepared {
    request: Request,
    coverage: Coverage,
    rows: Vec<TransitionRow>,
}
impl Prepared {
    pub fn request(&self) -> &Request {
        &self.request
    }
    pub fn coverage(&self) -> &Coverage {
        &self.coverage
    }
    pub fn transition_count(&self) -> usize {
        self.rows.len()
    }
}
fn source_matches(request: &Request, bar: &BarComplete) -> Result<()> {
    let source = bar.request();
    if source.instruments.as_slice() != [request.instrument]
        || source.provider != request.provider
        || source.session != request.session
        || source.interval != request.interval
        || source.timeframe_ns != BASE
        || source.hash()? != request.source_bar_request_hash
        || bar.coverage_hash() != request.source_bar_coverage_hash
    {
        return Err(Error::Conflict("Boolean source bar product differs".into()));
    }
    Ok(())
}
/// Pure preparation; no database access or product publication.
pub fn prepare_boolean_product(
    bar: &BarComplete,
    request: Request,
    dense: Vec<DenseBatch>,
    published_at_ns: u64,
) -> Result<Prepared> {
    request.validate()?;
    source_matches(&request, bar)?;
    if published_at_ns < request.interval.end || dense.is_empty() {
        return Err(Error::Unready(
            "Boolean product publication clock or batches".into(),
        ));
    }
    let mut digest = TransitionDigest::new(&request)?;
    let request_hash = request.hash()?;
    let mut rows = Vec::new();
    let mut state = None;
    let mut next_start = request.interval.start;
    for batch in &dense {
        let n = batch.known.len();
        if n == 0
            || n > PAGE_BUCKETS as usize
            || batch.first_start_ns != next_start
            || batch.evaluated.len() != n
            || batch.value.len() != n
        {
            return Err(Error::Conflict("Boolean preparation batch shape".into()));
        }
        next_start = next_start
            .checked_add(n as u64 * BASE)
            .ok_or_else(|| Error::Capacity("Boolean preparation clock".into()))?;
        if next_start > request.interval.end {
            return Err(Error::Conflict("Boolean preparation extent".into()));
        }
        for i in 0..n {
            let next = batch.known[i].then_some(batch.value[i]);
            if batch.value[i] && !batch.known[i] {
                return Err(Error::Conflict("Boolean true without known fact".into()));
            }
            if next != state {
                if !batch.evaluated[i] {
                    return Err(Error::Conflict(
                        "Boolean state changed without evaluation".into(),
                    ));
                }
                let at = batch.first_start_ns + i as u64 * BASE;
                digest.observe(at, batch.known[i], batch.value[i])?;
                rows.push(TransitionRow {
                    request_hash: request_hash.clone(),
                    provider: request.provider,
                    instrument: request.instrument,
                    session: request.session,
                    bucket_start_ns: at,
                    known: u8::from(batch.known[i]),
                    value: u8::from(batch.value[i]),
                });
            }
            state = next;
        }
    }
    if next_start != request.interval.end {
        return Err(Error::Unready("Boolean preparation incomplete".into()));
    }
    let (transition_hash, transition_count) = digest.finish();
    let coverage = Coverage {
        request_hash: request_hash.clone(),
        source_bar_coverage_hash: request.source_bar_coverage_hash.clone(),
        producer_hash: request.definition.implementation_hash.clone(),
        transition_hash,
        transition_count,
        published_at_ns,
    };
    let coverage_hash = coverage.hash()?;
    let mut readback = Readback::new(request.clone(), &coverage, published_at_ns)?;
    for batch in dense {
        readback.observe(Batch {
            request_hash: request_hash.clone(),
            coverage_hash: coverage_hash.clone(),
            first_start_ns: batch.first_start_ns,
            count: batch.known.len() as u32,
            evaluated: batch.evaluated,
            known: batch.known,
            value: batch.value,
        })?;
    }
    readback.finish()?;
    Ok(Prepared {
        request,
        coverage,
        rows,
    })
}
/// Pure fixed-cadence path from a complete columnar computation result to the
/// sparse ClickHouse publication contract. Rule evaluation itself is upstream.
pub fn prepare_calculated_boolean_product(
    bar: &BarComplete,
    request: Request,
    evaluations: &[arte_core::boolean_compute::Evaluation],
    published_at_ns: u64,
) -> Result<Prepared> {
    let dense = arte_core::boolean_compute::project_fixed(
        bar,
        &request,
        evaluations,
        PAGE_BUCKETS as usize,
    )?;
    prepare_boolean_product(bar, request, dense, published_at_ns)
}
pub fn boolean_publication_scope(request: &Request) -> Result<String> {
    arte_core::content_hash(&("arte.boolean-publication.v1", request.hash()?))
}

fn number<T: std::str::FromStr>(row: &Value, name: &str) -> Result<T> {
    let value = row
        .get(name)
        .ok_or_else(|| Error::Invalid(format!("Boolean {name} missing")))?;
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
        .parse()
        .map_err(|_| Error::Invalid(format!("Boolean {name} malformed")))
}
fn query(database: &str, request: &Request, start: u64, end: u64) -> Result<String> {
    identifier(database)?;
    request.validate()?;
    if start < request.interval.start
        || end > request.interval.end
        || start >= end
        || !start.is_multiple_of(BASE)
        || !end.is_multiple_of(BASE)
        || (end - start) / BASE > PAGE_BUCKETS
    {
        return Err(Error::Invalid("Boolean transition page".into()));
    }
    Ok(format!("SELECT request_hash,provider,instrument,session,bucket_start_ns,known,value FROM {database}.{TRANSITIONS} WHERE request_hash='{}' AND provider={} AND instrument={} AND session={} AND bucket_start_ns >= {start} AND bucket_start_ns < {end} ORDER BY bucket_start_ns LIMIT {} FORMAT JSONEachRow",
        request.hash()?,request.provider,request.instrument,request.session,PAGE_BUCKETS+1))
}
fn decode_row(line: &str) -> Result<TransitionRow> {
    let row: Value = serde_json::from_str(line)
        .map_err(|_| Error::Invalid("Boolean transition row JSON".into()))?;
    Ok(TransitionRow {
        request_hash: row
            .get("request_hash")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("Boolean request hash missing".into()))?
            .into(),
        provider: number(&row, "provider")?,
        instrument: number(&row, "instrument")?,
        session: number(&row, "session")?,
        bucket_start_ns: number(&row, "bucket_start_ns")?,
        known: number(&row, "known")?,
        value: number(&row, "value")?,
    })
}
fn rows_page(request: &Request, start: u64, end: u64, body: &str) -> Result<Vec<TransitionRow>> {
    let mut result = Vec::new();
    let mut prior = None;
    let expected_hash = request.hash()?;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.len() >= PAGE_BUCKETS as usize {
            return Err(Error::Capacity("Boolean page rows".into()));
        }
        let row = decode_row(line)?;
        if row.request_hash != expected_hash
            || row.provider != request.provider
            || row.instrument != request.instrument
            || row.session != request.session
            || row.bucket_start_ns < start
            || row.bucket_start_ns >= end
            || !row.bucket_start_ns.is_multiple_of(BASE)
            || prior.is_some_and(|at| row.bucket_start_ns <= at)
            || row.known > 1
            || row.value > 1
            || (row.known == 0 && row.value != 0)
        {
            return Err(Error::Conflict(
                "Boolean persisted row scope or order".into(),
            ));
        }
        prior = Some(row.bucket_start_ns);
        result.push(row);
    }
    Ok(result)
}
fn expand_page(
    request: &Request,
    coverage_hash: &str,
    start: u64,
    count: u32,
    body: &str,
    current: &mut Option<bool>,
    digest: &mut TransitionDigest,
) -> Result<Batch> {
    let end = start
        .checked_add(count as u64 * BASE)
        .ok_or_else(|| Error::Capacity("Boolean page clock".into()))?;
    let mut changes = std::collections::BTreeMap::new();
    let initial = *current;
    let mut last = initial;
    let mut previous = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if changes.len() >= PAGE_BUCKETS as usize {
            return Err(Error::Capacity("Boolean transition page rows".into()));
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("Boolean transition JSON".into()))?;
        let at: u64 = number(&row, "bucket_start_ns")?;
        let known: u8 = number(&row, "known")?;
        let value: u8 = number(&row, "value")?;
        if row.get("request_hash").and_then(Value::as_str) != Some(request.hash()?.as_str())
            || number::<u16>(&row, "provider")? != request.provider
            || number::<u64>(&row, "instrument")? != request.instrument
            || number::<u32>(&row, "session")? != request.session
            || at < start
            || at >= end
            || !(at - start).is_multiple_of(BASE)
            || previous.is_some_and(|prior| at <= prior)
            || known > 1
            || value > 1
            || (known == 0 && value != 0)
        {
            return Err(Error::Conflict(
                "Boolean transition row identity or order".into(),
            ));
        }
        let next = (known != 0).then_some(value != 0);
        if next == last {
            return Err(Error::Conflict("redundant Boolean transition".into()));
        }
        digest.observe(at, known != 0, value != 0)?;
        changes.insert(at, next);
        last = next;
        previous = Some(at);
    }
    // Rewind to the state before this page; scan transitions in bucket order.
    // The caller supplies it separately below so every bucket receives its
    // exact as-of state without dense database rows.
    let mut state = initial;
    let mut evaluated = Vec::with_capacity(count as usize);
    let mut known = Vec::with_capacity(count as usize);
    let mut value = Vec::with_capacity(count as usize);
    let ExecutionInterval::Fixed(cadence_ns) = request.definition.interval else {
        return Err(Error::Invalid(
            "Boolean sparse read requires fixed cadence".into(),
        ));
    };
    for i in 0..count {
        let at = start + i as u64 * BASE;
        if let Some(next) = changes.get(&at) {
            state = *next;
        }
        evaluated.push((at + BASE).is_multiple_of(cadence_ns));
        known.push(state.is_some());
        value.push(state.unwrap_or(false));
    }
    *current = state;
    Ok(Batch {
        request_hash: request.hash()?,
        coverage_hash: coverage_hash.into(),
        first_start_ns: start,
        count,
        evaluated,
        known,
        value,
    })
}

impl ClickHouse {
    async fn read_boolean_transitions(
        &self,
        request: &Request,
        start: u64,
        end: u64,
    ) -> Result<Vec<TransitionRow>> {
        let sql = query(&self.database, request, start, end)?;
        rows_page(
            request,
            start,
            end,
            &self.request(&sql, String::new()).await?,
        )
    }
    /// Insert immutable sparse transitions first, verify exact pages, then
    /// publish coverage. A partial write remains unready and is retryable.
    pub async fn publish_boolean_product(
        &self,
        prepared: &Prepared,
        source_bar: &BarComplete,
        now_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [
            Acceptance::RepositoryExtracted,
            Acceptance::SourceIdentity,
            Acceptance::EventStorage,
            Acceptance::StrategyParity,
            Acceptance::Durability,
        ] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "Boolean publication acceptance missing: {required:?}"
                )));
            }
        }
        source_matches(&prepared.request, source_bar)?;
        if now_ns < prepared.coverage.published_at_ns {
            return Err(Error::Invalid("Boolean publication clock regressed".into()));
        }
        lease.require(&boolean_publication_scope(&prepared.request)?)?;
        self.verify_storage(TRANSITIONS).await?;
        self.verify_storage(COVERAGE).await?;
        let mut start = prepared.request.interval.start;
        let mut first = 0usize;
        while start < prepared.request.interval.end {
            let end = prepared.request.interval.end.min(
                start
                    .checked_add(PAGE_BUCKETS * BASE)
                    .unwrap_or(prepared.request.interval.end),
            );
            let next =
                first + prepared.rows[first..].partition_point(|row| row.bucket_start_ns < end);
            let expected = &prepared.rows[first..next];
            let existing = self
                .read_boolean_transitions(&prepared.request, start, end)
                .await?;
            let mut by_start = BTreeMap::new();
            for row in existing {
                if by_start.insert(row.bucket_start_ns, row).is_some() {
                    return Err(Error::Conflict(
                        "duplicate Boolean transition persisted".into(),
                    ));
                }
            }
            if by_start.keys().any(|at| {
                expected
                    .binary_search_by_key(at, |row| row.bucket_start_ns)
                    .is_err()
            }) {
                return Err(Error::Conflict(
                    "unexpected Boolean transition persisted".into(),
                ));
            }
            let mut missing = Vec::new();
            for row in expected {
                match by_start.get(&row.bucket_start_ns) {
                    Some(existing) if existing == row => {}
                    Some(_) => {
                        return Err(Error::Conflict(
                            "immutable Boolean transition differs".into(),
                        ))
                    }
                    None => missing.push(
                        serde_json::to_value(row)
                            .map_err(|e| Error::Serialization(e.to_string()))?,
                    ),
                }
            }
            if !missing.is_empty() {
                self.insert(TRANSITIONS, &missing).await?;
            }
            if self
                .read_boolean_transitions(&prepared.request, start, end)
                .await?
                != expected
            {
                return Err(Error::Conflict(
                    "Boolean transition readback differs".into(),
                ));
            }
            first = next;
            start = end;
        }
        if first != prepared.rows.len() {
            return Err(Error::Conflict("Boolean unpublished suffix".into()));
        }
        let hash = prepared.coverage.hash()?;
        let payload = serde_json::to_string(&prepared.coverage)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if let Some(existing) = self
            .immutable_value(COVERAGE, "coverage_hash", &hash, "payload_json")
            .await?
        {
            if existing != payload {
                return Err(Error::Conflict("Boolean coverage already differs".into()));
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
            return Err(Error::Conflict("Boolean coverage readback differs".into()));
        }
        self.read_boolean_product(prepared.request.clone(), &hash, now_ns)
            .await?;
        Ok(hash)
    }
    /// The pinned coverage is obtained from an independently published catalogue.
    /// Storage policy and actual part placement are checked before the read.
    pub async fn read_boolean_product(
        &self,
        request: Request,
        expected_coverage_hash: &str,
        as_of_ns: u64,
    ) -> Result<Complete> {
        request.validate()?;
        TransitionDigest::new(&request)?;
        if expected_coverage_hash.len() != 64
            || !expected_coverage_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("Boolean expected coverage hash".into()));
        }
        self.verify_storage(TRANSITIONS).await?;
        self.verify_storage(COVERAGE).await?;
        let sql=format!("SELECT DISTINCT payload_json FROM {}.{COVERAGE} WHERE coverage_hash='{expected_coverage_hash}' LIMIT 2 FORMAT JSONEachRow",self.database);
        let body = self.request(&sql, String::new()).await?;
        let rows: Vec<Value> = body
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str(line)
                    .map_err(|_| Error::Invalid("Boolean coverage JSON".into()))
            })
            .collect::<Result<_>>()?;
        if rows.len() != 1 {
            return Err(Error::Unready(
                "Boolean coverage absent or conflicting".into(),
            ));
        }
        let payload = rows[0]
            .get("payload_json")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("Boolean coverage payload".into()))?;
        let coverage: Coverage = serde_json::from_str(payload)
            .map_err(|_| Error::Invalid("Boolean coverage payload JSON".into()))?;
        if coverage.hash()? != expected_coverage_hash {
            return Err(Error::Conflict("Boolean coverage identity differs".into()));
        }
        let mut readback = Readback::new(request, &coverage, as_of_ns)?;
        let mut digest = TransitionDigest::new(readback.request())?;
        let mut start = readback.request().interval.start;
        let end = readback.request().interval.end;
        let mut current = None;
        while start < end {
            let next = end.min(start.checked_add(PAGE_BUCKETS * BASE).unwrap_or(end));
            let sql = query(&self.database, readback.request(), start, next)?;
            let body = self.request(&sql, String::new()).await?;
            let batch = expand_page(
                readback.request(),
                expected_coverage_hash,
                start,
                ((next - start) / BASE) as u32,
                &body,
                &mut current,
                &mut digest,
            )?;
            readback.observe(batch)?;
            start = next;
        }
        let (hash, count) = digest.finish();
        if hash != coverage.transition_hash || count != coverage.transition_count {
            return Err(Error::Conflict("Boolean transition digest differs".into()));
        }
        readback.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        bar_catalogue::{
            Batch as BarBatch, Column, Coverage as BarCoverage, Readback as BarReadback,
            Request as BarRequest, Source,
        },
        coverage::Interval,
        execution_interval::{ExecutableKind, ExecutionContract},
    };
    use std::collections::BTreeSet;
    fn request() -> Request {
        Request {
            provider: 1,
            instrument: 10,
            session: 20260922,
            interval: Interval {
                start: 1_000_000_000,
                end: 1_400_000_000,
            },
            definition: ExecutionContract {
                kind: ExecutableKind::Watchlist,
                id: "tradability".into(),
                implementation_hash: "a".repeat(64),
                interval: ExecutionInterval::Fixed(200_000_000),
            },
            source_bar_request_hash: "b".repeat(64),
            source_bar_coverage_hash: "c".repeat(64),
            maximum_rows: 4,
        }
    }
    fn row(at: u64, known: u8, value: u8, r: &Request) -> String {
        serde_json::json!({"request_hash":r.hash().unwrap(),"provider":1,"instrument":10,
            "session":20260922,"bucket_start_ns":at,"known":known,"value":value})
        .to_string()
    }
    #[test]
    fn sparse_changes_expand_without_fabricating_unknown_state() {
        let r = request();
        let mut state = None;
        let mut digest = TransitionDigest::new(&r).unwrap();
        let body = format!(
            "{}\n{}",
            row(1_100_000_000, 1, 1, &r),
            row(1_300_000_000, 1, 0, &r)
        );
        let batch = expand_page(
            &r,
            &"d".repeat(64),
            1_000_000_000,
            4,
            &body,
            &mut state,
            &mut digest,
        )
        .unwrap();
        assert_eq!(batch.known, vec![false, true, true, true]);
        assert_eq!(batch.value, vec![false, true, true, false]);
        assert_eq!(batch.evaluated, vec![false, true, false, true]);
        assert_eq!(digest.finish().1, 2);
    }
    #[test]
    fn duplicate_off_grid_and_redundant_rows_fail() {
        let r = request();
        for body in [
            format!(
                "{}\n{}",
                row(1_100_000_000, 1, 1, &r),
                row(1_100_000_000, 1, 1, &r)
            ),
            row(1_200_000_000, 1, 1, &r),
            format!(
                "{}\n{}",
                row(1_100_000_000, 1, 1, &r),
                row(1_300_000_000, 1, 1, &r)
            ),
        ] {
            let mut state = None;
            let mut digest = TransitionDigest::new(&r).unwrap();
            assert!(expand_page(
                &r,
                &"d".repeat(64),
                1_000_000_000,
                4,
                &body,
                &mut state,
                &mut digest
            )
            .is_err());
        }
        assert!(query("bad-db", &r, 1_000_000_000, 1_100_000_000).is_err());
    }
    #[test]
    fn state_carries_across_sparse_pages() {
        let r = request();
        let mut current = None;
        let mut digest = TransitionDigest::new(&r).unwrap();
        let first = expand_page(
            &r,
            &"d".repeat(64),
            1_000_000_000,
            2,
            &row(1_100_000_000, 1, 1, &r),
            &mut current,
            &mut digest,
        )
        .unwrap();
        assert_eq!(first.known, vec![false, true]);
        let second = expand_page(
            &r,
            &"d".repeat(64),
            1_200_000_000,
            2,
            &row(1_300_000_000, 1, 0, &r),
            &mut current,
            &mut digest,
        )
        .unwrap();
        assert_eq!(second.known, vec![true, true]);
        assert_eq!(second.value, vec![true, false]);
        assert_eq!(current, Some(false));
    }
    fn bar() -> BarComplete {
        let interval = Interval {
            start: 1_000_000_000,
            end: 1_200_000_000,
        };
        let request = BarRequest {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval,
            timeframe_ns: BASE,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Close]),
            maximum_rows: 2,
        };
        let coverage = BarCoverage {
            provider: 1,
            session: 20260922,
            interval,
            timeframe_ns: BASE,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                10,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 2,
                },
            )]),
            published_at_ns: 2_000_000_000,
        };
        let mut read = BarReadback::new(request.clone(), &coverage, 2_000_000_000).unwrap();
        read.observe(BarBatch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument: 10,
            first_start_ns: interval.start,
            count: 2,
            price_scale: 2,
            size_scale: 2,
            present: vec![true, true],
            open: None,
            high: None,
            low: None,
            close: Some(vec![1000, 1100]),
            volume: None,
            notional: None,
            trades: None,
        })
        .unwrap();
        read.finish().unwrap()
    }
    #[test]
    fn preparation_pins_source_and_rejects_off_cadence_change() {
        let source = bar();
        let mut r = request();
        r.interval = source.request().interval;
        r.source_bar_request_hash = source.request().hash().unwrap();
        r.source_bar_coverage_hash = source.coverage_hash().into();
        r.maximum_rows = 2;
        let valid = DenseBatch {
            first_start_ns: r.interval.start,
            evaluated: vec![false, true],
            known: vec![false, true],
            value: vec![false, true],
        };
        let prepared = prepare_boolean_product(&source, r, vec![valid], 2_000_000_000).unwrap();
        assert_eq!(prepared.transition_count(), 1);
        assert_eq!(prepared.coverage().transition_count, 1);
        let mut wrong = prepared.request().clone();
        wrong.source_bar_coverage_hash = "d".repeat(64);
        assert!(prepare_boolean_product(
            &source,
            wrong,
            vec![DenseBatch {
                first_start_ns: 1_000_000_000,
                evaluated: vec![false, true],
                known: vec![false, true],
                value: vec![false, true]
            }],
            2_000_000_000
        )
        .is_err());
        let invalid = DenseBatch {
            first_start_ns: 1_000_000_000,
            evaluated: vec![false, true],
            known: vec![true, true],
            value: vec![true, true],
        };
        assert!(prepare_boolean_product(
            &source,
            prepared.request().clone(),
            vec![invalid],
            2_000_000_000
        )
        .is_err());
    }
    #[test]
    fn calculated_grid_enters_sparse_publication_without_unknown_as_false() {
        let source = bar();
        let mut r = request();
        r.interval = source.request().interval;
        r.source_bar_request_hash = source.request().hash().unwrap();
        r.source_bar_coverage_hash = source.coverage_hash().into();
        r.maximum_rows = 2;
        let due = 1_100_000_000;
        let evaluation = arte_core::boolean_compute::Evaluation {
            bucket_start_ns: due,
            value: None,
        };
        let unknown =
            prepare_calculated_boolean_product(&source, r.clone(), &[evaluation], 2_000_000_000)
                .unwrap();
        assert_eq!(unknown.transition_count(), 0);
        let known = prepare_calculated_boolean_product(
            &source,
            r.clone(),
            &[arte_core::boolean_compute::Evaluation {
                bucket_start_ns: due,
                value: Some(false),
            }],
            2_000_000_000,
        )
        .unwrap();
        assert_eq!(known.transition_count(), 1);
        assert_ne!(
            unknown.coverage().transition_hash,
            known.coverage().transition_hash
        );
        assert!(prepare_calculated_boolean_product(&source, r, &[], 2_000_000_000).is_err());
    }
}
