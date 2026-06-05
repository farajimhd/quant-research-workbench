use crate::config::NewsGatewayConfig;
use reqwest::Client;
use std::path::PathBuf;
use tokio::process::Command;
use tokio::time::{timeout, Duration};

#[derive(Clone, Debug)]
pub struct ExtractionResult {
    pub body_text: String,
    pub extracted_text: String,
    pub extraction_error: String,
    pub extraction_status: String,
    pub pdf_texts: Vec<String>,
    pub pdf_urls: Vec<String>,
    pub url_enriched: bool,
}

pub async fn extract_content(
    client: &Client,
    config: &NewsGatewayConfig,
    body_html: &str,
    article_url: &str,
) -> ExtractionResult {
    let body_text = normalize_text(&strip_html(body_html));
    let mut extracted_text = body_text.clone();
    let mut extraction_status = if body_text.is_empty() { "title_only" } else { "not_needed" }.to_string();
    let mut extraction_error = String::new();
    let mut pdf_urls = extract_pdf_urls(body_html);
    let mut pdf_texts = Vec::new();
    let mut url_enriched = false;

    if config.extraction_enabled && body_text.len() < config.extraction_min_body_chars && !article_url.trim().is_empty() {
        match fetch_text(client, article_url, config.extraction_timeout_ms).await {
            Ok(html) => {
                for url in extract_pdf_urls(&html) {
                    if !pdf_urls.contains(&url) {
                        pdf_urls.push(url);
                    }
                }
                let fetched_text = normalize_text(&strip_html(&html));
                if fetched_text.len() > extracted_text.len() {
                    extracted_text = fetched_text;
                    extraction_status = "url_enriched".to_string();
                    url_enriched = true;
                }
            }
            Err(error) => {
                extraction_error = error;
                extraction_status = "url_failed".to_string();
            }
        }
    }

    if config.pdf_extraction_enabled {
        for pdf_url in pdf_urls.iter().take(3) {
            match extract_pdf_text(client, pdf_url, config).await {
                Ok(text) if !text.trim().is_empty() => {
                    pdf_texts.push(text.clone());
                    if !extracted_text.is_empty() {
                        extracted_text.push('\n');
                    }
                    extracted_text.push_str(&text);
                    extraction_status = "pdf_enriched".to_string();
                }
                Ok(_) => {}
                Err(error) => {
                    if extraction_error.is_empty() {
                        extraction_error = error;
                    }
                }
            }
        }
    }

    ExtractionResult {
        body_text,
        extracted_text,
        extraction_error,
        extraction_status,
        pdf_texts,
        pdf_urls,
        url_enriched,
    }
}

pub fn strip_html(input: &str) -> String {
    let mut output = String::with_capacity(input.len());
    let mut in_tag = false;
    for ch in input.chars() {
        match ch {
            '<' => {
                in_tag = true;
                output.push(' ');
            }
            '>' => {
                in_tag = false;
                output.push(' ');
            }
            _ if !in_tag => output.push(ch),
            _ => {}
        }
    }
    decode_basic_entities(&output)
}

pub fn normalize_text(input: &str) -> String {
    input.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn url_domain(url: &str) -> String {
    let Some(rest) = url.split_once("://").map(|(_, rest)| rest) else {
        return String::new();
    };
    rest.split('/').next().unwrap_or_default().to_ascii_lowercase()
}

pub fn stable_hash(parts: &[&str]) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for part in parts {
        for byte in part.as_bytes() {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
        hash ^= 0xff;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

fn decode_basic_entities(input: &str) -> String {
    input
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#8217;", "'")
        .replace("&#8220;", "\"")
        .replace("&#8221;", "\"")
        .replace("&#8211;", "-")
        .replace("&#8212;", "-")
}

fn extract_pdf_urls(input: &str) -> Vec<String> {
    let mut urls = Vec::new();
    for token in input.split(['"', '\'', ' ', '<', '>']) {
        let trimmed = token.trim().trim_end_matches('\\');
        if trimmed.to_ascii_lowercase().contains(".pdf")
            && (trimmed.starts_with("http://") || trimmed.starts_with("https://"))
            && !urls.iter().any(|item| item == trimmed)
        {
            urls.push(trimmed.to_string());
        }
    }
    urls
}

async fn fetch_text(client: &Client, url: &str, timeout_ms: u64) -> Result<String, String> {
    let response = timeout(Duration::from_millis(timeout_ms), client.get(url).send())
        .await
        .map_err(|_| "url_fetch_timeout".to_string())?
        .map_err(|error| error.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("url_fetch_http_{status}"));
    }
    response.text().await.map_err(|error| error.to_string())
}

async fn extract_pdf_text(client: &Client, url: &str, config: &NewsGatewayConfig) -> Result<String, String> {
    let response = timeout(Duration::from_millis(config.extraction_timeout_ms), client.get(url).send())
        .await
        .map_err(|_| "pdf_fetch_timeout".to_string())?
        .map_err(|error| error.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(format!("pdf_fetch_http_{status}"));
    }
    let bytes = response.bytes().await.map_err(|error| error.to_string())?;
    if bytes.len() > config.pdf_max_bytes {
        return Err("pdf_too_large".to_string());
    }
    let input_path = temp_pdf_path("news-gateway-input", "pdf");
    let output_path = temp_pdf_path("news-gateway-output", "txt");
    tokio::fs::write(&input_path, &bytes).await.map_err(|error| error.to_string())?;
    let output = Command::new("pdftotext")
        .arg("-layout")
        .arg(&input_path)
        .arg(&output_path)
        .output()
        .await
        .map_err(|_| "pdftotext_not_available".to_string())?;
    if !output.status.success() {
        let _ = tokio::fs::remove_file(&input_path).await;
        let _ = tokio::fs::remove_file(&output_path).await;
        return Err("pdftotext_failed".to_string());
    }
    let text = tokio::fs::read_to_string(&output_path)
        .await
        .map_err(|error| error.to_string())?;
    let _ = tokio::fs::remove_file(&input_path).await;
    let _ = tokio::fs::remove_file(&output_path).await;
    Ok(normalize_text(&text))
}

fn temp_pdf_path(prefix: &str, ext: &str) -> PathBuf {
    let mut path = std::env::temp_dir();
    let unique = format!(
        "{}-{}-{}.{}",
        prefix,
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default(),
        ext
    );
    path.push(unique);
    path
}
