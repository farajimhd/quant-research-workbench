use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SecFilingEvent {
    pub session_date: NaiveDate,
    pub schema_version: u16,
    pub provider: String,
    pub event_type: String,
    pub event_id: String,
    pub cik: String,
    pub company_name: String,
    pub accession_number: String,
    pub accession_number_compact: String,
    pub form_type: String,
    pub filing_date: Option<NaiveDate>,
    pub accepted_at_utc: Option<DateTime<Utc>>,
    pub feed_updated_at_utc: Option<DateTime<Utc>>,
    pub gateway_seen_at_utc: DateTime<Utc>,
    pub feed_url: String,
    pub detail_url: String,
    pub primary_document: String,
    pub primary_document_url: String,
    pub document_count: usize,
    pub parsed_document_count: usize,
    pub extraction_status: String,
    pub extraction_error: String,
    pub artifact_root: String,
    pub raw_feed_artifact_path: String,
    pub detail_artifact_path: String,
    pub raw_feed_json: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SecFilingDocument {
    pub session_date: NaiveDate,
    pub schema_version: u16,
    pub event_id: String,
    pub cik: String,
    pub accession_number: String,
    pub sequence: usize,
    pub document_name: String,
    pub document_type: String,
    pub description: String,
    pub document_url: String,
    pub content_type: String,
    pub byte_length: usize,
    pub content_sha256: String,
    pub artifact_path: String,
    pub text_hash: String,
    pub extracted_text: String,
    pub extraction_status: String,
    pub extraction_error: String,
    pub downloaded_at_utc: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SecGatewayMessage {
    pub event: SecFilingEvent,
    pub documents: Vec<SecFilingDocument>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SecFilingSummary {
    pub event_id: String,
    pub cik: String,
    pub company_name: String,
    pub accession_number: String,
    pub form_type: String,
    pub filing_date: Option<NaiveDate>,
    pub accepted_at_utc: Option<DateTime<Utc>>,
    pub gateway_seen_at_utc: DateTime<Utc>,
    pub detail_url: String,
    pub primary_document_url: String,
    pub document_count: usize,
    pub parsed_document_count: usize,
    pub extraction_status: String,
}

impl From<&SecFilingEvent> for SecFilingSummary {
    fn from(event: &SecFilingEvent) -> Self {
        Self {
            event_id: event.event_id.clone(),
            cik: event.cik.clone(),
            company_name: event.company_name.clone(),
            accession_number: event.accession_number.clone(),
            form_type: event.form_type.clone(),
            filing_date: event.filing_date.clone(),
            accepted_at_utc: event.accepted_at_utc.clone(),
            gateway_seen_at_utc: event.gateway_seen_at_utc.clone(),
            detail_url: event.detail_url.clone(),
            primary_document_url: event.primary_document_url.clone(),
            document_count: event.document_count,
            parsed_document_count: event.parsed_document_count,
            extraction_status: event.extraction_status.clone(),
        }
    }
}
