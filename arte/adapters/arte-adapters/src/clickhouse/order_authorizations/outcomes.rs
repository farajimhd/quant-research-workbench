use super::*;
use arte_core::orders::{outcome::Record, submission::Marker};
const TABLE: &str = "broker_initial_outcomes_v1";
const MAX_PAYLOAD: usize = 300 * 1024;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct OutcomeRow {
    submission_hash: String,
    record_hash: String,
    payload_json: String,
}
fn decode(body: &str, marker: &Marker, expected: &Record) -> Result<Option<Record>> {
    expected.require(marker)?;
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("broker outcome readback byte limit".into()));
    }
    let canonical =
        serde_json::to_string(expected).map_err(|e| Error::Serialization(e.to_string()))?;
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict("multiple broker outcome versions".into()));
        }
        let row: OutcomeRow =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.submission_hash != expected.submission_hash
            || row.record_hash != expected.hash()?
            || row.payload_json != canonical
        {
            return Err(Error::Conflict("broker outcome readback differs".into()));
        }
        result = Some(expected.clone());
    }
    Ok(result)
}
impl ClickHouse {
    async fn find_initial_outcome(
        &self,
        marker: &Marker,
        expected: &Record,
    ) -> Result<Option<Record>> {
        expected.require(marker)?;
        self.verify_storage(TABLE).await?;
        let query = format!("SELECT DISTINCT submission_hash,record_hash,payload_json FROM {}.{TABLE} WHERE submission_hash='{}' LIMIT 2 FORMAT JSONEachRow", self.database, marker.hash()?);
        decode(
            &self.request(&query, String::new()).await?,
            marker,
            expected,
        )
    }
}
impl crate::order_journal::OutcomePublisher for OrderPublisher<'_> {
    async fn append_outcome(
        &mut self,
        authorization: &Authorization,
        marker: &Marker,
        record: &Record,
    ) -> Result<Record> {
        self.lease.require(&self.ownership_hash)?;
        if authorization.bracket.account != self.account {
            return Err(Error::Conflict("broker outcome account mismatch".into()));
        }
        marker.require(authorization)?;
        record.require(marker)?;
        if self.database.find_submission(marker).await?.is_none() {
            return Err(Error::Unready(
                "submission marker missing before outcome".into(),
            ));
        }
        let payload_json =
            serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("broker outcome payload limit".into()));
        }
        if let Some(existing) = self.database.find_initial_outcome(marker, record).await? {
            return Ok(existing);
        }
        let row = OutcomeRow {
            submission_hash: marker.hash()?,
            record_hash: record.hash()?,
            payload_json,
        };
        self.database
            .insert(
                TABLE,
                &[serde_json::to_value(row).map_err(|e| Error::Serialization(e.to_string()))?],
            )
            .await?;
        self.database
            .find_initial_outcome(marker, record)
            .await?
            .ok_or_else(|| Error::Unready("broker outcome readback missing".into()))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn broker_outcome_storage_rejects_conflicting_versions_and_noncanonical_payload() {
        let marker = Marker {
            order_key: "a".repeat(64),
            authorization_hash: "b".repeat(64),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        };
        let expected = Record {
            submission_hash: marker.hash().unwrap(),
            observed_at_ns: 11,
            observation: arte_core::orders::outcome::Observation::Response {
                status: 200,
                body: vec![255; 65536],
            },
        };
        let mut row = OutcomeRow {
            submission_hash: expected.submission_hash.clone(),
            record_hash: expected.hash().unwrap(),
            payload_json: serde_json::to_string(&expected).unwrap(),
        };
        assert!(row.payload_json.len() <= MAX_PAYLOAD);
        let body = serde_json::to_string(&row).unwrap();
        assert_eq!(
            decode(&body, &marker, &expected).unwrap(),
            Some(expected.clone())
        );
        assert!(decode("", &marker, &expected).unwrap().is_none());
        assert!(decode(&format!("{body}\n{body}"), &marker, &expected).is_err());
        row.payload_json.push(' ');
        assert!(decode(&serde_json::to_string(&row).unwrap(), &marker, &expected).is_err());
    }
}
