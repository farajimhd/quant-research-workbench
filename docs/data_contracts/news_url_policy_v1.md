# `q_live.news_url_policy_v1`

This compact table stores the URL/domain policy used by the operational news pipeline. It replaces runtime dependence on the exploratory URL inventory.

The table is small. It stores rules such as "registered domain `benzinga.com` -> `ignore`" or "registered domain `t.co` -> `resolve_redirect`"; it does not store every historical URL occurrence.

## Table Shape

```sql
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (provider, policy_version, match_type, match_value, policy_id)
```

Columns:

```sql
policy_id String,
policy_version LowCardinality(String),
provider LowCardinality(String),
match_type LowCardinality(String),
match_value String,
action LowCardinality(String),
priority Int32,
enabled UInt8,
reason String,
source LowCardinality(String),
created_at_utc DateTime64(9, 'UTC'),
updated_at_utc DateTime64(9, 'UTC')
```

## Actions

| Action | Meaning |
| --- | --- |
| `ignore` | Do not fetch. Usually internal provider, quote, ad, or tracking links. |
| `metadata_only` | Keep the URL relation but do not fetch content. Usually social, video, image, webinar, or low-value hosted pages. |
| `fetch_html` | Fetch and extract HTML text. |
| `fetch_pdf` | Fetch and extract PDF text under the size/importance policy. |
| `resolve_redirect` | Resolve a shortener/redirector before deciding the final fetch action. |
| `sec_handler` | Route to the SEC pipeline instead of normal article enrichment. |
| `review` | Keep for manual review; do not assume production fetch behavior. |

## Seeder

Dry run:

```powershell
python -m pipelines.news.benzinga.news_benzinga_url_policy
```

Create and seed:

```powershell
python -m pipelines.news.benzinga.news_benzinga_url_policy --execute --seed-default
```

Audit:

```powershell
python -m pipelines.news.benzinga.news_benzinga_url_policy --audit-only
```

The first seed loaded `67` default policy rows for `benzinga-url-domain-policy-v1`.
