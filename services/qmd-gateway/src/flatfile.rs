use crate::config::GatewayConfig;
use chrono::{Datelike, NaiveDate, Utc};
use reqwest::{Client, StatusCode};
use ring::{digest, hmac};

#[derive(Clone, Debug)]
pub struct RemoteFlatfile {
    pub content_length: u64,
    pub etag: String,
    pub key: String,
    pub last_modified: String,
}

#[derive(Clone)]
pub struct FlatfileDiscovery {
    client: Client,
    config: GatewayConfig,
}

impl FlatfileDiscovery {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn discover(
        &self,
        date: NaiveDate,
        kind: &str,
    ) -> Result<Option<RemoteFlatfile>, String> {
        if self.config.flatfile_access_key.is_empty()
            || self.config.flatfile_secret_key().is_empty()
        {
            return Err("Massive flatfile S3 credentials are not configured".into());
        }
        let family = if kind == "quote" {
            "quotes_v1"
        } else {
            "trades_v1"
        };
        let key = format!(
            "us_stocks_sip/{family}/{:04}/{:02}/{date}.csv.gz",
            date.year(),
            date.month()
        );
        let endpoint = self.config.flatfile_endpoint_url.trim_end_matches('/');
        let canonical_uri = format!("/{}/{}", self.config.flatfile_bucket, key);
        let url = format!("{endpoint}{canonical_uri}");
        let now = Utc::now();
        let amz_date = now.format("%Y%m%dT%H%M%SZ").to_string();
        let date_stamp = now.format("%Y%m%d").to_string();
        let host = reqwest::Url::parse(endpoint)
            .map_err(|error| error.to_string())?
            .host_str()
            .ok_or("flatfile endpoint has no host")?
            .to_string();
        let payload_hash = hex(digest::digest(&digest::SHA256, &[]).as_ref());
        let canonical_headers =
            format!("host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n");
        let signed_headers = "host;x-amz-content-sha256;x-amz-date";
        let canonical_request = format!(
            "HEAD\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        );
        let scope = format!(
            "{date_stamp}/{}/s3/aws4_request",
            self.config.flatfile_region
        );
        let string_to_sign = format!(
            "AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{}",
            hex(digest::digest(&digest::SHA256, canonical_request.as_bytes()).as_ref())
        );
        let signature = aws_signature(
            &self.config.flatfile_secret_key(),
            &date_stamp,
            &self.config.flatfile_region,
            &string_to_sign,
        )?;
        let authorization = format!("AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders={signed_headers}, Signature={signature}", self.config.flatfile_access_key);
        let response = self
            .client
            .head(url)
            .header("Host", host)
            .header("x-amz-content-sha256", payload_hash)
            .header("x-amz-date", amz_date)
            .header("Authorization", authorization)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(format!(
                "flatfile HEAD {} returned {}",
                key,
                response.status()
            ));
        }
        let headers = response.headers();
        Ok(Some(RemoteFlatfile {
            content_length: headers
                .get("content-length")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse().ok())
                .unwrap_or(0),
            etag: headers
                .get("etag")
                .and_then(|value| value.to_str().ok())
                .unwrap_or_default()
                .trim_matches('"')
                .to_string(),
            key,
            last_modified: headers
                .get("last-modified")
                .and_then(|value| value.to_str().ok())
                .unwrap_or_default()
                .to_string(),
        }))
    }
}

fn aws_signature(
    secret: &str,
    date: &str,
    region: &str,
    string_to_sign: &str,
) -> Result<String, String> {
    fn sign(key: &[u8], data: &str) -> Vec<u8> {
        hmac::sign(&hmac::Key::new(hmac::HMAC_SHA256, key), data.as_bytes())
            .as_ref()
            .to_vec()
    }
    let date_key = sign(format!("AWS4{secret}").as_bytes(), date);
    let region_key = sign(&date_key, region);
    let service_key = sign(&region_key, "s3");
    let signing_key = sign(&service_key, "aws4_request");
    Ok(hex(&sign(&signing_key, string_to_sign)))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aws_signing_key_matches_independent_hmac_vector() {
        let signature = aws_signature(
            "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            "20260713",
            "us-east-1",
            "test-string",
        )
        .unwrap();
        assert_eq!(
            signature,
            "84ec7dca20aacd3345df397983d4a6a7b47a740025cd74fbce76100bf9b31bea"
        );
    }
}
