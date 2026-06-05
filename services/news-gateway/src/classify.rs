use crate::model::{Classification, NEWS_FILTER_VERSION};

pub fn classify_news(
    title: &str,
    body_text: &str,
    extracted_text: &str,
    tickers: &[String],
    channels: &[String],
    tags: &[String],
    keywords: &[String],
    insight_sentiments: &[String],
    has_pdf: bool,
    url_enriched: bool,
) -> Classification {
    let mut labels = catalyst_labels(title, channels, tags, keywords);
    let content_scope = content_scope(tickers, channels, title);
    let content_completeness = content_completeness(body_text, extracted_text, has_pdf, url_enriched);
    let scanner_relevance = scanner_relevance(tickers, channels, &labels, insight_sentiments);
    let model_relevance = model_relevance(&content_scope, &labels, tickers, title);
    labels.sort();
    labels.dedup();
    let quality_outcome = if content_completeness == "title_only" {
        "accepted_low_content"
    } else if scanner_relevance == "high" || scanner_relevance == "medium" {
        "accepted_actionable"
    } else {
        "accepted_context"
    };
    Classification {
        catalyst_labels: labels,
        content_completeness,
        content_scope,
        model_relevance,
        quality_outcome: quality_outcome.to_string(),
        scanner_relevance,
    }
}

pub fn filter_version() -> u16 {
    NEWS_FILTER_VERSION
}

fn content_scope(tickers: &[String], channels: &[String], title: &str) -> String {
    let title_l = title.to_ascii_lowercase();
    let channels_l = channels
        .iter()
        .map(|item| item.to_ascii_lowercase())
        .collect::<Vec<_>>();
    let equity_count = tickers.iter().filter(|ticker| is_equity_like(ticker)).count();
    if tickers.iter().all(|ticker| ticker.starts_with("X:")) && !tickers.is_empty() {
        "crypto"
    } else if channels_l.iter().any(|item| item.contains("economics") || item.contains("econ #")) {
        "macro"
    } else if channels_l.iter().any(|item| item.contains("politics") || item.contains("government"))
        || title_l.contains("war")
        || title_l.contains("geopolitical")
        || title_l.contains("tariff")
    {
        "geopolitical"
    } else if equity_count == 1 {
        "single_equity"
    } else if equity_count > 1 {
        "multi_equity"
    } else if tickers.iter().any(|ticker| is_etf_like(ticker)) {
        "etf"
    } else if tickers.is_empty() {
        "no_ticker_context"
    } else {
        "broad_market"
    }
    .to_string()
}

fn content_completeness(body_text: &str, extracted_text: &str, has_pdf: bool, url_enriched: bool) -> String {
    if has_pdf && !extracted_text.trim().is_empty() {
        "pdf_enriched".to_string()
    } else if url_enriched && !extracted_text.trim().is_empty() {
        "url_enriched".to_string()
    } else if body_text.trim().len() >= 300 {
        "full_body".to_string()
    } else if !body_text.trim().is_empty() {
        "short_body".to_string()
    } else {
        "title_only".to_string()
    }
}

fn scanner_relevance(
    tickers: &[String],
    channels: &[String],
    labels: &[String],
    insight_sentiments: &[String],
) -> String {
    let has_equity = tickers.iter().any(|ticker| is_equity_like(ticker));
    let channels_l = channels
        .iter()
        .map(|item| item.to_ascii_lowercase())
        .collect::<Vec<_>>();
    let high_channel = channels_l.iter().any(|item| {
        matches!(
            item.as_str(),
            "movers" | "earnings" | "guidance" | "analyst ratings" | "price target" | "m&a" | "hot" | "top stories"
        )
    });
    if has_equity && (high_channel || !labels.is_empty()) {
        "high"
    } else if has_equity || !insight_sentiments.is_empty() {
        "medium"
    } else {
        "low"
    }
    .to_string()
}

fn model_relevance(content_scope: &str, labels: &[String], tickers: &[String], title: &str) -> String {
    let title_l = title.to_ascii_lowercase();
    if !labels.is_empty()
        || !tickers.is_empty()
        || matches!(content_scope, "macro" | "geopolitical" | "crypto")
        || title_l.contains("war")
        || title_l.contains("fed")
        || title_l.contains("inflation")
    {
        "high"
    } else {
        "medium"
    }
    .to_string()
}

fn catalyst_labels(title: &str, channels: &[String], tags: &[String], keywords: &[String]) -> Vec<String> {
    let haystack = format!(
        "{} {} {} {}",
        title,
        channels.join(" "),
        tags.join(" "),
        keywords.join(" ")
    )
    .to_ascii_lowercase();
    let mut labels = Vec::new();
    let checks = [
        ("earnings", &["earnings", "eps", "revenue", "quarterly results"][..]),
        ("guidance", &["guidance", "outlook", "forecast"][..]),
        ("analyst_action", &["analyst ratings", "price target", "upgrade", "downgrade", "initiates", "reiterates"][..]),
        ("mna", &["merger", "acquisition", "m&a", "buyout", "takeover"][..]),
        ("offering_dilution", &["offering", "public offering", "registered direct", "warrant", "atm offering"][..]),
        ("fda_biotech", &["fda", "phase 1", "phase 2", "phase 3", "clinical", "pdufa"][..]),
        ("halt_regulatory", &["halt", "resumes trading", "sec", "investigation"][..]),
        ("contract_partnership", &["contract", "partnership", "collaboration", "selected by"][..]),
        ("macro", &["cpi", "ppi", "pmi", "jobs report", "jobless claims", "fed", "fomc", "inflation"][..]),
        ("geopolitical", &["war", "tariff", "sanction", "geopolitical", "conflict"][..]),
        ("crypto", &["bitcoin", "ethereum", "crypto", "blockchain"][..]),
    ];
    for (label, needles) in checks {
        if needles.iter().any(|needle| haystack.contains(needle)) {
            labels.push(label.to_string());
        }
    }
    labels
}

fn is_equity_like(ticker: &str) -> bool {
    let value = ticker.trim();
    !value.starts_with("X:")
        && !value.contains(':')
        && value.chars().all(|ch| ch.is_ascii_uppercase() || ch == '.')
        && (1..=6).contains(&value.len())
}

fn is_etf_like(ticker: &str) -> bool {
    matches!(
        ticker,
        "SPY" | "QQQ" | "IWM" | "DIA" | "EWA" | "EWJ" | "DXJ" | "SLV" | "GLD" | "TLT" | "HYG"
    )
}
