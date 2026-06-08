use crate::config::SecGatewayConfig;
use crate::model::{SecFilingDocument, SecFilingEvent, SecFilingSummary, SecGatewayMessage};
use crate::state::SharedSecState;
use blake2::{Blake2b512, Digest};
use chrono::{DateTime, NaiveDate, Utc};
use feed_rs::parser;
use futures_util::StreamExt;
use regex::Regex;
use reqwest::Client;
use scraper::{Html, Selector};
use serde_json::json;
use sha2::Sha256;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;
use tokio::time::{sleep, Duration};
use url::Url;

#[derive(Clone, Debug)]
struct FeedFiling {
    cik: String,
    company_name: String,
    accession_number: String,
    accession_number_compact: String,
    form_type: String,
    filing_date: Option<NaiveDate>,
    feed_updated_at_utc: Option<DateTime<Utc>>,
    detail_url: String,
    raw_feed_json: String,
}

#[derive(Clone, Debug)]
struct FilingDocumentLink {
    sequence: usize,
    document_name: String,
    document_type: String,
    description: String,
    document_url: String,
}

pub async fn run_sec_feed_poller(config: SecGatewayConfig, state: SharedSecState, sender: mpsc::Sender<SecGatewayMessage>) {
    let user_agent = config.user_agent();
    if user_agent.is_empty() {
        eprintln!("SEC gateway warning: set SEC_USER_AGENT/NEWS_SEC_USER_AGENT/SEC_EDGAR_USER_AGENT before production runs.");
    }
    let client = Client::builder()
        .timeout(Duration::from_millis(config.request_timeout_ms))
        .user_agent(if user_agent.is_empty() {
            "QuantResearchWorkbench/0.1 contact@example.com".to_string()
        } else {
            user_agent
        })
        .build()
        .expect("SEC HTTP client");

    loop {
        match poll_once(&client, &config, &state, &sender).await {
            Ok(count) => {
                if count > 0 {
                    eprintln!("SEC gateway processed {count} new filing events");
                }
            }
            Err(error) => eprintln!("SEC gateway poll failed: {error}"),
        }
        sleep(Duration::from_millis(config.feed_poll_interval_ms)).await;
    }
}

async fn poll_once(
    client: &Client,
    config: &SecGatewayConfig,
    state: &SharedSecState,
    sender: &mpsc::Sender<SecGatewayMessage>,
) -> Result<usize, String> {
    let feed_bytes = fetch_bytes(client, &config.feed_url, config.document_max_bytes).await?;
    let feed = parser::parse(&feed_bytes[..]).map_err(|error| error.to_string())?;
    let mut processed = 0usize;
    for entry in feed.entries {
        let Some(filing) = filing_from_entry(&entry, &config.feed_url) else {
            continue;
        };
        let filing_key = filing_state_key(&filing);
        if state.has_seen(&filing_key).await {
            continue;
        }
        match build_message(client, config, filing).await {
            Ok(message) => {
                state.mark_seen(&filing_key).await;
                state.push_recent(SecFilingSummary::from(&message.event)).await;
                if sender.send(message).await.is_err() {
                    return Err("SEC writer channel closed".to_string());
                }
                processed += 1;
            }
            Err(error) => eprintln!("SEC filing build failed: {error}"),
        }
    }
    Ok(processed)
}

fn filing_from_entry(entry: &feed_rs::model::Entry, feed_url: &str) -> Option<FeedFiling> {
    let title = entry.title.as_ref().map(|value| value.content.clone()).unwrap_or_default();
    let summary = entry.summary.as_ref().map(|value| value.content.clone()).unwrap_or_default();
    let summary_plain = html_fragment_text(&summary);
    let content_text = entry
        .content
        .as_ref()
        .and_then(|value| value.body.clone())
        .unwrap_or_default();
    let content_plain = html_fragment_text(&content_text);
    let haystack = format!("{title}\n{summary}\n{summary_plain}\n{content_text}\n{content_plain}");
    let detail_url = entry
        .links
        .iter()
        .find(|link| !link.href.trim().is_empty())
        .map(|link| link.href.clone())
        .unwrap_or_default();
    let accession_number = capture_first(
        &haystack,
        &[
            r"Accession(?: Number)?:\s*([0-9]{10}-[0-9]{2}-[0-9]{6})",
            r"AccNo:\s*([0-9]{10}-[0-9]{2}-[0-9]{6})",
            r"accession_number=([0-9]{10}-[0-9]{2}-[0-9]{6})",
            r"accessionNumber=([0-9]{10}-[0-9]{2}-[0-9]{6})",
        ],
    )
    .or_else(|| accession_from_url(&detail_url))?;
    let cik = capture_first(
        &haystack,
        &[
            r"CIK:\s*([0-9]{1,10})",
            r"CIK=([0-9]{1,10})",
            r"cik=([0-9]{1,10})",
            r"\(([0-9]{10})\)",
        ],
    )
    .unwrap_or_else(|| cik_from_url(&detail_url).unwrap_or_default());
    let form_type = capture_first(&haystack, &[r"Form Type:\s*([^<\n\r]+)", r"<category[^>]*term=\"([^\"]+)\""])
        .or_else(|| entry.categories.first().map(|category| category.term.clone()))
        .unwrap_or_default()
        .trim()
        .to_string();
    let filing_date = capture_first(
        &haystack,
        &[
            r"Filing Date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            r"Filed:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
        ],
    )
    .and_then(|value| NaiveDate::parse_from_str(&value, "%Y-%m-%d").ok());
    let company_name = company_from_title(&title);
    Some(FeedFiling {
        cik: normalize_cik(&cik),
        company_name,
        accession_number: accession_number.clone(),
        accession_number_compact: accession_number.replace('-', ""),
        form_type,
        filing_date,
        feed_updated_at_utc: entry
            .updated
            .clone()
            .or_else(|| entry.published.clone())
            .map(|value| value.with_timezone(&Utc)),
        detail_url,
        raw_feed_json: json!({
            "feed_url": feed_url,
            "id": entry.id.clone(),
            "title": title,
            "summary": summary,
            "content": content_text,
            "links": entry.links.iter().map(|link| link.href.clone()).collect::<Vec<_>>(),
            "categories": entry.categories.iter().map(|category| category.term.clone()).collect::<Vec<_>>(),
            "updated": entry.updated.as_ref().map(|value| value.to_rfc3339()),
            "published": entry.published.as_ref().map(|value| value.to_rfc3339()),
        })
        .to_string(),
    })
}

fn filing_state_key(filing: &FeedFiling) -> String {
    format!("{}:{}", filing.cik, filing.accession_number)
}

async fn build_message(client: &Client, config: &SecGatewayConfig, filing: FeedFiling) -> Result<SecGatewayMessage, String> {
    let gateway_seen_at_utc = Utc::now();
    let session_date = gateway_seen_at_utc.date_naive();
    let event_id = format!("sec:{}:{}", filing.cik, filing.accession_number);
    let artifact_root = PathBuf::from(&config.artifact_root_win);
    let event_dir = artifact_root
        .join("raw")
        .join(gateway_seen_at_utc.format("%Y").to_string())
        .join(gateway_seen_at_utc.format("%m").to_string())
        .join(gateway_seen_at_utc.format("%d").to_string())
        .join(&filing.cik)
        .join(&filing.accession_number_compact);
    tokio::fs::create_dir_all(&event_dir).await.map_err(|error| error.to_string())?;
    let raw_feed_artifact_path = event_dir.join("feed_item.json");
    tokio::fs::write(&raw_feed_artifact_path, filing.raw_feed_json.as_bytes())
        .await
        .map_err(|error| error.to_string())?;

    let detail_html = fetch_text(client, &filing.detail_url, config.document_max_bytes).await.unwrap_or_default();
    let detail_artifact_path = event_dir.join("filing_detail.html");
    if !detail_html.is_empty() {
        tokio::fs::write(&detail_artifact_path, detail_html.as_bytes())
            .await
            .map_err(|error| error.to_string())?;
    }
    let accepted_at_utc = match accepted_at_from_detail(&detail_html) {
        Some(value) => Some(value),
        None => accepted_at_from_accession_text(client, config, &filing, &event_dir).await,
    };
    let links = document_links(&filing.detail_url, &detail_html);
    let documents = download_documents(client, config, session_date, &event_id, &filing, &event_dir, &links).await;
    let parsed_document_count = documents
        .iter()
        .filter(|document| document.extraction_status == "extracted")
        .count();
    let primary_document = links.first().map(|link| link.document_name.clone()).unwrap_or_default();
    let primary_document_url = links.first().map(|link| link.document_url.clone()).unwrap_or_default();
    let extraction_status = if links.is_empty() {
        "no_documents"
    } else if parsed_document_count > 0 {
        "partial_or_complete"
    } else {
        "metadata_only"
    };
    let event = SecFilingEvent {
        session_date,
        schema_version: 1,
        provider: "sec".to_string(),
        event_type: "sec_filing".to_string(),
        event_id,
        cik: filing.cik.clone(),
        company_name: filing.company_name,
        accession_number: filing.accession_number,
        accession_number_compact: filing.accession_number_compact,
        form_type: filing.form_type,
        filing_date: filing.filing_date,
        accepted_at_utc,
        feed_updated_at_utc: filing.feed_updated_at_utc,
        gateway_seen_at_utc,
        feed_url: config.feed_url.clone(),
        detail_url: filing.detail_url,
        primary_document,
        primary_document_url,
        document_count: links.len(),
        parsed_document_count,
        extraction_status: extraction_status.to_string(),
        extraction_error: String::new(),
        artifact_root: config.artifact_root_win.clone(),
        raw_feed_artifact_path: raw_feed_artifact_path.to_string_lossy().to_string(),
        detail_artifact_path: detail_artifact_path.to_string_lossy().to_string(),
        raw_feed_json: filing.raw_feed_json,
    };
    Ok(SecGatewayMessage { event, documents })
}

async fn download_documents(
    client: &Client,
    config: &SecGatewayConfig,
    session_date: NaiveDate,
    event_id: &str,
    filing: &FeedFiling,
    event_dir: &Path,
    links: &[FilingDocumentLink],
) -> Vec<SecFilingDocument> {
    let mut output = Vec::new();
    for link in links.iter().take(config.max_documents_per_filing) {
        let downloaded_at_utc = Utc::now();
        let mut status = "download_failed".to_string();
        let mut error = String::new();
        let mut bytes = Vec::new();
        match fetch_bytes(client, &link.document_url, config.document_max_bytes).await {
            Ok(value) => {
                bytes = value;
                status = "downloaded".to_string();
            }
            Err(fetch_error) => error = fetch_error,
        }
        let content_type = content_type_from_name(&link.document_name);
        let artifact_path = event_dir.join("documents").join(safe_file_name(&link.document_name));
        if !bytes.is_empty() {
            if let Some(parent) = artifact_path.parent() {
                let _ = tokio::fs::create_dir_all(parent).await;
            }
            if let Err(write_error) = tokio::fs::write(&artifact_path, &bytes).await {
                error = write_error.to_string();
                status = "artifact_write_failed".to_string();
            }
        }
        let extracted_text = if status == "downloaded" {
            extract_text(&bytes, &link.document_name)
        } else {
            String::new()
        };
        if !extracted_text.is_empty() {
            status = "extracted".to_string();
        } else if status == "downloaded" && content_type == "application/pdf" {
            status = "pdf_text_not_supported".to_string();
        } else if status == "downloaded" {
            status = "empty_text".to_string();
        }
        output.push(SecFilingDocument {
            session_date,
            schema_version: 1,
            event_id: event_id.to_string(),
            cik: filing.cik.clone(),
            accession_number: filing.accession_number.clone(),
            sequence: link.sequence,
            document_name: link.document_name.clone(),
            document_type: link.document_type.clone(),
            description: link.description.clone(),
            document_url: link.document_url.clone(),
            content_type,
            byte_length: bytes.len(),
            content_sha256: sha256_hex(&bytes),
            artifact_path: artifact_path.to_string_lossy().to_string(),
            text_hash: blake2_hex(extracted_text.as_bytes()),
            extracted_text,
            extraction_status: status,
            extraction_error: error,
            downloaded_at_utc,
        });
    }
    output
}

async fn fetch_text(client: &Client, url: &str, max_bytes: usize) -> Result<String, String> {
    let bytes = fetch_bytes(client, url, max_bytes).await?;
    Ok(String::from_utf8_lossy(&bytes).to_string())
}

async fn fetch_bytes(client: &Client, url: &str, max_bytes: usize) -> Result<Vec<u8>, String> {
    let response = client
        .get(url)
        .send()
        .await
        .map_err(|error| error.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("HTTP {status}"));
    }
    if let Some(content_length) = response.content_length() {
        if content_length as usize > max_bytes {
            return Err(format!("document_too_large:{content_length}>{max_bytes}"));
        }
    }
    let mut output = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| error.to_string())?;
        if output.len() + chunk.len() > max_bytes {
            return Err(format!(
                "document_too_large:{}>{}",
                output.len() + chunk.len(),
                max_bytes
            ));
        }
        output.extend_from_slice(&chunk);
    }
    Ok(output)
}

fn document_links(detail_url: &str, html: &str) -> Vec<FilingDocumentLink> {
    if html.trim().is_empty() {
        return Vec::new();
    }
    let base = Url::parse(detail_url).ok();
    let doc = Html::parse_document(html);
    let row_selector = Selector::parse("tr").unwrap();
    let cell_selector = Selector::parse("td").unwrap();
    let link_selector = Selector::parse("a").unwrap();
    let mut links = Vec::new();
    for row in doc.select(&row_selector) {
        let cells: Vec<_> = row.select(&cell_selector).collect();
        if cells.len() < 3 {
            continue;
        }
        let Some(anchor) = cells[2].select(&link_selector).next() else {
            continue;
        };
        let Some(href) = anchor.value().attr("href") else {
            continue;
        };
        if href.contains("-index.") || href.contains("/ixviewer/") {
            continue;
        }
        let document_url = absolute_url(base.as_ref(), href);
        if document_url.is_empty() {
            continue;
        }
        let sequence = cells
            .first()
            .map(|cell| cell.text().collect::<String>().trim().parse::<usize>().unwrap_or(0))
            .unwrap_or(0);
        let document_type = cells
            .get(3)
            .map(|cell| normalize_space(&cell.text().collect::<String>()))
            .unwrap_or_default();
        let description = cells
            .get(1)
            .map(|cell| normalize_space(&cell.text().collect::<String>()))
            .unwrap_or_default();
        let document_name = anchor.text().collect::<String>().trim().to_string();
        if document_name.is_empty() {
            continue;
        }
        links.push(FilingDocumentLink {
            sequence,
            document_name,
            document_type,
            description,
            document_url,
        });
    }
    links
}

fn accepted_at_from_detail(html: &str) -> Option<DateTime<Utc>> {
    let re = Regex::new(r"Accepted\s*</div>\s*<div[^>]*>\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})").ok()?;
    let value = re.captures(html)?.get(1)?.as_str();
    chrono::NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S")
        .ok()
        .map(|value| DateTime::<Utc>::from_naive_utc_and_offset(value, Utc))
}

async fn accepted_at_from_accession_text(
    client: &Client,
    config: &SecGatewayConfig,
    filing: &FeedFiling,
    event_dir: &Path,
) -> Option<DateTime<Utc>> {
    let url = accession_text_url(filing)?;
    let text = fetch_text(client, &url, config.document_max_bytes).await.ok()?;
    let path = event_dir.join("accession.txt");
    let _ = tokio::fs::write(path, text.as_bytes()).await;
    accepted_at_from_accession_text_body(&text)
}

fn accepted_at_from_accession_text_body(text: &str) -> Option<DateTime<Utc>> {
    let re = Regex::new(r"(?m)^ACCEPTANCE-DATETIME:\s*([0-9]{14})\s*$").ok()?;
    let value = re.captures(text)?.get(1)?.as_str();
    chrono::NaiveDateTime::parse_from_str(value, "%Y%m%d%H%M%S")
        .ok()
        .map(|value| DateTime::<Utc>::from_naive_utc_and_offset(value, Utc))
}

fn accession_text_url(filing: &FeedFiling) -> Option<String> {
    let cik_unpadded = filing.cik.trim_start_matches('0');
    if cik_unpadded.is_empty() || filing.accession_number_compact.is_empty() {
        return None;
    }
    Some(format!(
        "https://www.sec.gov/Archives/edgar/data/{}/{}/{}.txt",
        cik_unpadded, filing.accession_number_compact, filing.accession_number
    ))
}

fn extract_text(bytes: &[u8], name: &str) -> String {
    let lower = name.to_ascii_lowercase();
    if lower.ends_with(".pdf") || lower.ends_with(".jpg") || lower.ends_with(".png") || lower.ends_with(".gif") {
        return String::new();
    }
    let text = String::from_utf8_lossy(bytes).to_string();
    if lower.ends_with(".htm") || lower.ends_with(".html") || text.contains("<html") || text.contains("<HTML") {
        let doc = Html::parse_document(&text);
        let selector = Selector::parse("body").unwrap();
        let body = doc
            .select(&selector)
            .next()
            .map(|node| node.text().collect::<Vec<_>>().join(" "))
            .unwrap_or_else(|| doc.root_element().text().collect::<Vec<_>>().join(" "));
        normalize_space(&body)
    } else {
        normalize_space(&text)
    }
}

fn html_fragment_text(value: &str) -> String {
    if value.trim().is_empty() {
        return String::new();
    }
    let doc = Html::parse_fragment(value);
    normalize_space(&doc.root_element().text().collect::<Vec<_>>().join(" "))
}

fn capture_first(text: &str, patterns: &[&str]) -> Option<String> {
    for pattern in patterns {
        let re = Regex::new(pattern).ok()?;
        if let Some(value) = re.captures(text).and_then(|captures| captures.get(1)) {
            return Some(value.as_str().trim().to_string());
        }
    }
    None
}

fn company_from_title(title: &str) -> String {
    let value = title.split_once(" - ").map(|(_, company)| company).unwrap_or(title);
    let re = Regex::new(r"\s*\([0-9]{1,10}\)\s*\([^)]*\)\s*$").ok();
    match re {
        Some(re) => re.replace(value, "").trim().to_string(),
        None => value.trim().to_string(),
    }
}

fn accession_from_url(url: &str) -> Option<String> {
    let re = Regex::new(r"([0-9]{10}-[0-9]{2}-[0-9]{6})").ok()?;
    re.captures(url).and_then(|captures| captures.get(1)).map(|value| value.as_str().to_string())
}

fn cik_from_url(url: &str) -> Option<String> {
    let re = Regex::new(r"/data/([0-9]+)/").ok()?;
    re.captures(url).and_then(|captures| captures.get(1)).map(|value| normalize_cik(value.as_str()))
}

fn normalize_cik(value: &str) -> String {
    format!("{:0>10}", value.trim().trim_start_matches('0')).chars().rev().take(10).collect::<String>().chars().rev().collect()
}

fn absolute_url(base: Option<&Url>, href: &str) -> String {
    if let Ok(url) = Url::parse(href) {
        return url.to_string();
    }
    base.and_then(|base| base.join(href).ok()).map(|url| url.to_string()).unwrap_or_default()
}

fn content_type_from_name(name: &str) -> String {
    let lower = name.to_ascii_lowercase();
    if lower.ends_with(".pdf") {
        "application/pdf"
    } else if lower.ends_with(".xml") || lower.ends_with(".xsd") {
        "application/xml"
    } else if lower.ends_with(".htm") || lower.ends_with(".html") {
        "text/html"
    } else if lower.ends_with(".txt") {
        "text/plain"
    } else {
        "application/octet-stream"
    }
    .to_string()
}

fn safe_file_name(value: &str) -> String {
    let cleaned = value
        .chars()
        .map(|ch| if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '_') { ch } else { '_' })
        .collect::<String>();
    if cleaned.trim_matches('_').is_empty() {
        "document".to_string()
    } else {
        cleaned
    }
}

fn normalize_space(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn blake2_hex(bytes: &[u8]) -> String {
    let mut hasher = Blake2b512::new();
    hasher.update(bytes);
    let hash = hasher.finalize();
    hex_prefix(&hash, 16)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let hash = hasher.finalize();
    hex_prefix(&hash, hash.len())
}

fn hex_prefix(bytes: &[u8], len: usize) -> String {
    bytes.iter().take(len).map(|byte| format!("{byte:02x}")).collect::<String>()
}
