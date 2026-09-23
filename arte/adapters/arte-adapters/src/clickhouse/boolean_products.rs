//! Read sparse Boolean transitions only under pinned complete coverage.
use super::*;
use arte_core::boolean_catalogue::{
    Batch, Complete, Coverage, Readback, Request, TransitionDigest,
};
use arte_core::execution_interval::ExecutionInterval;

const TRANSITIONS: &str = "boolean_transitions_v1";
const COVERAGE: &str = "boolean_coverage_v1";
const PAGE_BUCKETS: u64 = 1_000;
const BASE: u64 = arte_core::bar_catalogue::BASE_INTERVAL_NS;

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
        coverage::Interval,
        execution_interval::{ExecutableKind, ExecutionContract},
    };
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
}
