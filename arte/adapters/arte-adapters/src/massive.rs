use arte_core::events::*;
use arte_core::{Error, Result};
use chrono::{Datelike, TimeZone, Utc};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::time::Duration;

pub struct FetchedPage {
    pub rows: Vec<Value>,
    pub next_url: Option<String>,
    pub request_hash: String,
    pub response_hash: String,
    pub acquired_at_ns: u64,
}

fn field<'a>(v: &'a Value, name: &str) -> Result<&'a Value> {
    v.get(name)
        .ok_or_else(|| Error::Invalid(format!("missing Massive field {name}")))
}
fn integer(v: &Value, name: &str) -> Result<u64> {
    field(v, name)?
        .as_u64()
        .ok_or_else(|| Error::Invalid(format!("invalid integer {name}")))
}
fn small(v: &Value, name: &str) -> Result<u16> {
    u16::try_from(integer(v, name)?).map_err(|_| Error::Invalid(format!("overflow {name}")))
}
fn decimal(v: &Value, name: &str) -> Result<Decimal> {
    let value = field(v, name)?;
    Decimal::parse(
        &value
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| value.to_string()),
    )
}
fn codes(v: &Value, name: &str) -> Result<Vec<u16>> {
    let Some(value) = v.get(name) else {
        return Ok(vec![]);
    };
    let items: Vec<&Value> = if let Some(a) = value.as_array() {
        a.iter().collect()
    } else {
        vec![value]
    };
    items
        .into_iter()
        .map(|x| {
            x.as_u64()
                .and_then(|n| u16::try_from(n).ok())
                .ok_or_else(|| Error::Invalid(format!("invalid condition {name}")))
        })
        .collect()
}
fn source_time(v: &Value, name: &str, factor: u64) -> Result<SourceTime> {
    Ok(SourceTime {
        ns: integer(v, name)?
            .checked_mul(factor)
            .ok_or_else(|| Error::Invalid("timestamp overflow".into()))?,
        precision_ns: factor as u32,
    })
}
fn session(sip: SourceTime) -> Result<u32> {
    let seconds = i64::try_from(sip.ns / 1_000_000_000)
        .map_err(|_| Error::Invalid("timestamp overflow".into()))?;
    let time = Utc
        .timestamp_opt(seconds, (sip.ns % 1_000_000_000) as u32)
        .single()
        .ok_or_else(|| Error::Invalid("invalid epoch".into()))?
        .with_timezone(&chrono_tz::America::New_York);
    Ok(time.year() as u32 * 10000 + time.month() * 100 + time.day())
}

/// Point-in-time symbol-to-instrument resolution is supplied by the reference authority.
pub fn normalize(
    v: &Value,
    instrument: u64,
    kind: EventKind,
    websocket: bool,
    available_at_ns: u64,
    receipt: Option<Receipt>,
) -> Result<Observation> {
    if websocket != receipt.is_some() {
        return Err(Error::Invalid(
            "live input requires receipt; REST must not fabricate it".into(),
        ));
    }
    let factor = if websocket { 1_000_000 } else { 1 };
    let sip = source_time(v, if websocket { "t" } else { "sip_timestamp" }, factor)?;
    let participant_field = if websocket {
        "pt"
    } else {
        "participant_timestamp"
    };
    let participant = if v.get(participant_field).is_some_and(|x| !x.is_null()) {
        Some(source_time(v, participant_field, factor)?)
    } else {
        None
    };
    let sequence = integer(v, if websocket { "q" } else { "sequence_number" })?;
    let payload = match kind {
        EventKind::Trade => Payload::Trade {
            price: decimal(v, if websocket { "p" } else { "price" })?,
            size: decimal(
                v,
                if websocket {
                    if v.get("ds").is_some() {
                        "ds"
                    } else {
                        "s"
                    }
                } else if v.get("decimal_size").is_some() {
                    "decimal_size"
                } else {
                    "size"
                },
            )?,
            exchange: small(v, if websocket { "x" } else { "exchange" })?,
            trade_id: field(v, if websocket { "i" } else { "id" })?
                .as_str()
                .ok_or_else(|| Error::Invalid("trade ID must be string".into()))?
                .to_owned(),
            trf: if v.get(if websocket { "trfi" } else { "trf_id" }).is_some() {
                Some(small(v, if websocket { "trfi" } else { "trf_id" })?)
            } else {
                None
            },
            conditions: codes(v, if websocket { "c" } else { "conditions" })?,
            correction: if v.get("correction").is_some() {
                Some(
                    u8::try_from(integer(v, "correction")?)
                        .map_err(|_| Error::Invalid("correction overflow".into()))?,
                )
            } else {
                None
            },
        },
        EventKind::Quote => Payload::Quote {
            bid: decimal(v, if websocket { "bp" } else { "bid_price" })?,
            ask: decimal(v, if websocket { "ap" } else { "ask_price" })?,
            bid_size: decimal(v, if websocket { "bs" } else { "bid_size" })?,
            ask_size: decimal(v, if websocket { "as" } else { "ask_size" })?,
            bid_exchange: small(v, if websocket { "bx" } else { "bid_exchange" })?,
            ask_exchange: small(v, if websocket { "ax" } else { "ask_exchange" })?,
            conditions: codes(v, if websocket { "c" } else { "conditions" })?,
            indicators: codes(v, if websocket { "i" } else { "indicators" })?,
        },
    };
    let event = Observation {
        key: EventKey {
            provider: 1,
            instrument,
            session: session(sip)?,
            kind,
            sequence,
        },
        payload,
        sip,
        participant,
        available_at_ns,
        receipt,
    };
    event.validate()?;
    Ok(event)
}

/// Reject pagination URLs outside the exact authenticated provider origin and path.
pub fn validate_page_url(url: &str, expected_path: &str) -> Result<reqwest::Url> {
    let mut parsed =
        reqwest::Url::parse(url).map_err(|_| Error::Invalid("invalid pagination URL".into()))?;
    if parsed.scheme() != "https"
        || parsed.host_str() != Some("api.massive.com")
        || parsed.port_or_known_default() != Some(443)
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.path() != expected_path
        || parsed.fragment().is_some()
    {
        return Err(Error::Invalid(
            "pagination URL outside provider scope".into(),
        ));
    }
    let params: Vec<_> = parsed
        .query_pairs()
        .filter(|(key, _)| key != "apiKey")
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    parsed.set_query(None);
    parsed.query_pairs_mut().extend_pairs(params);
    Ok(parsed)
}

pub struct RestClient {
    http: reqwest::Client,
    key: String,
    max_pages: usize,
}
impl RestClient {
    /// One bounded request. Returned cursor is authenticated-origin/path checked and
    /// stripped of apiKey. Caller retains the current URL until its page is durable.
    pub async fn fetch_page(&self, url: &str, expected_path: &str) -> Result<FetchedPage> {
        if !(expected_path.starts_with("/v3/trades/") || expected_path.starts_with("/v3/quotes/")) {
            return Err(Error::Invalid("unsupported historical endpoint".into()));
        }
        let url = validate_page_url(url, expected_path)?;
        let request_hash = arte_core::content_hash(&url.as_str())?;
        let mut response = self
            .http
            .get(url)
            .bearer_auth(&self.key)
            .send()
            .await
            .map_err(|_| Error::Unready("Massive request failed; credentials redacted".into()))?;
        if !response.status().is_success() {
            return Err(Error::Unready(format!(
                "Massive HTTP {}",
                response.status().as_u16()
            )));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| Error::Unready("Massive response interrupted".into()))?
        {
            if bytes.len().saturating_add(chunk.len()) > 32 * 1024 * 1024 {
                return Err(Error::Capacity(
                    "REST page exceeds 32 MiB; interval remains uncertified".into(),
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        let acquired_at_ns = u64::try_from(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| Error::Invalid("acquisition UTC before epoch".into()))?
                .as_nanos(),
        )
        .map_err(|_| Error::Invalid("acquisition UTC overflow".into()))?;
        let response_hash = format!("{:x}", Sha256::digest(&bytes));
        let (rows, next_url) = decode_page(&bytes)?;
        let next_url = next_url
            .map(|next| validate_page_url(&next, expected_path).map(|url| url.to_string()))
            .transpose()?;
        Ok(FetchedPage {
            rows,
            next_url,
            request_hash,
            response_hash,
            acquired_at_ns,
        })
    }
    pub fn new(key: String, max_pages: usize) -> Result<Self> {
        if key.is_empty() || max_pages == 0 {
            return Err(Error::Invalid(
                "REST credential and page budget required".into(),
            ));
        }
        Ok(Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|_| Error::Invalid("HTTP client configuration".into()))?,
            key,
            max_pages,
        })
    }
    /// Each page is handed off before the next request. A failed callback stops acquisition.
    pub async fn pages<F>(&self, first: &str, mut consume: F) -> Result<usize>
    where
        F: FnMut(&[Value]) -> Result<()>,
    {
        let path = reqwest::Url::parse(first)
            .map_err(|_| Error::Invalid("invalid REST URL".into()))?
            .path()
            .to_string();
        if !(path.starts_with("/v3/trades/") || path.starts_with("/v3/quotes/")) {
            return Err(Error::Invalid("unsupported historical endpoint".into()));
        }
        let mut next = Some(first.to_string());
        let mut visited = BTreeSet::new();
        let mut pages = 0;
        while let Some(url) = next {
            if pages >= self.max_pages {
                return Err(Error::Capacity(
                    "REST page budget; interval remains uncertified".into(),
                ));
            }
            let url = validate_page_url(&url, &path)?;
            if !visited.insert(url.to_string()) {
                return Err(Error::Conflict("pagination loop".into()));
            }
            let fetched = self.fetch_page(url.as_str(), &path).await?;
            consume(&fetched.rows)?;
            pages += 1;
            next = fetched.next_url;
        }
        Ok(pages)
    }
}
pub fn decode_page(bytes: &[u8]) -> Result<(Vec<Value>, Option<String>)> {
    let body: Value = serde_json::from_slice(bytes)
        .map_err(|_| Error::Invalid("invalid Massive response JSON".into()))?;
    if body.get("status").and_then(Value::as_str) != Some("OK") {
        return Err(Error::Unready("Massive response not OK".into()));
    }
    let rows = match body.get("results") {
        None => vec![],
        Some(Value::Array(rows)) if rows.len() <= 50_000 => rows.clone(),
        _ => return Err(Error::Invalid("invalid or oversized result array".into())),
    };
    let next = match body.get("next_url") {
        None | Some(Value::Null) => None,
        Some(Value::String(url)) if !url.is_empty() => Some(url.clone()),
        _ => return Err(Error::Invalid("invalid pagination cursor".into())),
    };
    Ok((rows, next))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_page_is_not_empty_coverage() {
        assert!(decode_page(br#"{"status":"OK","results":{}}"#).is_err());
        assert!(decode_page(br#"{"status":"OK","next_url":42}"#).is_err());
        assert!(decode_page(br#"{"status":"ERROR"}"#).is_err());
        assert_eq!(
            decode_page(br#"{"status":"OK","results":[]}"#)
                .unwrap()
                .0
                .len(),
            0
        );
    }
    #[test]
    fn normalizes_precision_and_fractional_size() {
        let value = serde_json::json!({"price":10.01,"decimal_size":"0.125","exchange":4,"id":"a","sequence_number":104,"sip_timestamp":1757943000000000000u64,"participant_timestamp":1757942999999000000u64});
        let event = normalize(
            &value,
            1,
            EventKind::Trade,
            false,
            1757943001000000000,
            None,
        )
        .unwrap();
        assert_eq!(event.sip.precision_ns, 1);
        if let Payload::Trade { size, .. } = event.payload {
            assert_eq!(
                size,
                Decimal {
                    atoms: 125,
                    scale: 3
                }
            );
        } else {
            panic!()
        }
    }
    #[test]
    fn rejects_foreign_pagination() {
        assert!(validate_page_url("https://evil.test/v3/trades/AAPL", "/v3/trades/AAPL").is_err());
        assert!(
            validate_page_url("https://api.massive.com/v3/quotes/AAPL", "/v3/trades/AAPL").is_err()
        );
    }
}
