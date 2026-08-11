use axum::body::Body;
use axum::http::{HeaderMap, HeaderName, HeaderValue, Request};
use axum::middleware::Next;
use axum::response::Response;

const CORRELATION_HEADER: HeaderName = HeaderName::from_static("x-correlation-id");
const CAUSATION_HEADER: HeaderName = HeaderName::from_static("x-causation-id");

pub async fn preserve_request_identity(request: Request<Body>, next: Next) -> Response {
    let query = request.uri().query();
    let correlation = request_identity(request.headers(), &CORRELATION_HEADER)
        .or_else(|| query_identity(query, "correlation_id"));
    let causation = request_identity(request.headers(), &CAUSATION_HEADER)
        .or_else(|| query_identity(query, "causation_id"))
        .or_else(|| correlation.clone());
    let mut response = next.run(request).await;
    if let Some(value) = correlation {
        response.headers_mut().insert(CORRELATION_HEADER, value);
    }
    if let Some(value) = causation {
        response.headers_mut().insert(CAUSATION_HEADER, value);
    }
    response
}

fn request_identity(headers: &HeaderMap, name: &HeaderName) -> Option<HeaderValue> {
    request_identity_value(headers.get(name)?.to_str().ok()?)
}

fn query_identity(query: Option<&str>, key: &str) -> Option<HeaderValue> {
    query?
        .split('&')
        .filter_map(|part| part.split_once('='))
        .find_map(|(candidate, value)| {
            if candidate != key {
                return None;
            }
            let decoded = urlencoding::decode(value).ok()?;
            request_identity_value(decoded.as_ref())
        })
}

fn request_identity_value(value: &str) -> Option<HeaderValue> {
    let value = value.trim();
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
    {
        return None;
    }
    HeaderValue::from_str(value).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_identity_accepts_bounded_transport_safe_values() {
        let mut headers = HeaderMap::new();
        headers.insert(
            &CORRELATION_HEADER,
            HeaderValue::from_static("web:run-1.a_b"),
        );
        assert_eq!(
            request_identity(&headers, &CORRELATION_HEADER)
                .unwrap()
                .to_str()
                .unwrap(),
            "web:run-1.a_b"
        );
    }

    #[test]
    fn request_identity_rejects_unbounded_or_unsafe_values() {
        let mut headers = HeaderMap::new();
        headers.insert(
            &CORRELATION_HEADER,
            HeaderValue::from_static("unsafe value"),
        );
        assert!(request_identity(&headers, &CORRELATION_HEADER).is_none());
        headers.insert(
            &CORRELATION_HEADER,
            HeaderValue::from_str(&"a".repeat(129)).unwrap(),
        );
        assert!(request_identity(&headers, &CORRELATION_HEADER).is_none());
    }

    #[test]
    fn websocket_query_identity_uses_the_same_validation() {
        assert_eq!(
            query_identity(
                Some("limit=25&correlation_id=web%3Arequest-18&causation_id=chart-7"),
                "correlation_id"
            )
            .unwrap()
            .to_str()
            .unwrap(),
            "web:request-18"
        );
        assert!(query_identity(Some("correlation_id=unsafe%20value"), "correlation_id").is_none());
    }
}
