use super::*;
use arte_core::{
    acquisition::{Authority, Catalog, Certificate},
    content_hash,
    coverage::Interval,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
const TABLE: &str = "event_coverage_index_v1";
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Row {
    authority_hash: String,
    interval_start: u64,
    interval_end: u64,
    published_at_ns: u64,
    certificate_hash: String,
}
fn hash_ok(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn ids(body: &str, maximum: usize) -> Result<Vec<String>> {
    let mut result = vec![];
    let mut unique = BTreeSet::new();
    for line in body.lines().filter(|s| !s.trim().is_empty()) {
        if result.len() == maximum {
            return Err(Error::Capacity("coverage discovery exceeded bound".into()));
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("coverage index response malformed".into()))?;
        let id = row
            .get("certificate_hash")
            .and_then(Value::as_str)
            .filter(|id| hash_ok(id))
            .ok_or_else(|| Error::Invalid("coverage index identity malformed".into()))?;
        if !unique.insert(id.to_owned()) {
            return Err(Error::Conflict("duplicate coverage discovery row".into()));
        }
        result.push(id.to_owned());
    }
    Ok(result)
}
impl ClickHouse {
    pub(super) async fn publish_coverage_index(&self, certificate: &Certificate) -> Result<()> {
        self.verify_storage(TABLE).await?;
        let row = Row {
            authority_hash: content_hash(&certificate.authority)?,
            interval_start: certificate.interval.start,
            interval_end: certificate.interval.end,
            published_at_ns: certificate.published_at_ns,
            certificate_hash: certificate.id()?,
        };
        self.insert(
            TABLE,
            &[serde_json::to_value(&row).map_err(|e| Error::Serialization(e.to_string()))?],
        )
        .await?;
        let query = format!("SELECT DISTINCT authority_hash,interval_start,interval_end,published_at_ns,certificate_hash FROM {}.{TABLE} WHERE authority_hash='{}' AND certificate_hash='{}' LIMIT 2 SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow", self.database, row.authority_hash, row.certificate_hash);
        let body = self.request(&query, String::new()).await?;
        let rows: Vec<Row> = body
            .lines()
            .filter(|s| !s.trim().is_empty())
            .map(|s| {
                serde_json::from_str(s)
                    .map_err(|_| Error::Invalid("coverage index readback malformed".into()))
            })
            .collect::<Result<_>>()?;
        if rows != vec![row] {
            return Err(Error::Conflict("coverage index readback differs".into()));
        }
        Ok(())
    }
    /// Index matches are candidates, never coverage. Reload every certificate and
    /// all referenced batches before publishing into the returned catalog.
    pub async fn discover_acquisitions(
        &self,
        authority: &Authority,
        interval: Interval,
        as_of_ns: u64,
        maximum: usize,
    ) -> Result<Catalog> {
        interval.validate()?;
        if maximum == 0
            || maximum > 4096
            || as_of_ns < interval.end
            || authority.instrument == 0
            || authority.provider == 0
        {
            return Err(Error::Invalid("coverage discovery scope or bound".into()));
        }
        self.verify_storage(TABLE).await?;
        let authority_hash = content_hash(authority)?;
        let query = format!("SELECT DISTINCT certificate_hash FROM {}.{TABLE} WHERE authority_hash='{authority_hash}' AND interval_start<{} AND interval_end>{} AND published_at_ns<={as_of_ns} ORDER BY certificate_hash LIMIT {} FORMAT JSONEachRow", self.database, interval.end, interval.start, maximum + 1);
        let body = self.request(&query, String::new()).await?;
        let identities = ids(&body, maximum)?;
        let mut catalog = Catalog::new(maximum)?;
        for id in identities {
            let verified = self.load_acquisition(&id).await?;
            let certificate = verified.certificate();
            if &certificate.authority != authority
                || certificate.published_at_ns > as_of_ns
                || certificate.interval.start >= interval.end
                || certificate.interval.end <= interval.start
            {
                return Err(Error::Conflict(
                    "coverage discovery index disagrees with certificate".into(),
                ));
            }
            catalog.publish(verified)?;
        }
        Ok(catalog)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn discovery_rejects_overflow_duplicates_and_malformed_ids() {
        let line = serde_json::json!({"certificate_hash":"a".repeat(64)}).to_string();
        assert_eq!(ids(&line, 1).unwrap(), vec!["a".repeat(64)]);
        assert!(matches!(
            ids(&format!("{line}\n{line}"), 1),
            Err(Error::Capacity(_))
        ));
        assert!(matches!(
            ids(&format!("{line}\n{line}"), 2),
            Err(Error::Conflict(_))
        ));
        assert!(ids("{\"certificate_hash\":\"' OR 1=1\"}", 1).is_err());
        assert!(ids("not-json", 1).is_err());
    }
}
