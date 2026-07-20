# Private Cloud Run viewer

This deployment keeps report objects in a private Cloud Storage bucket and
mounts that bucket read-only in Cloud Run. Direct Cloud Run IAP authenticates
the browser; do not grant `allUsers` either the Run Invoker or IAP accessor
role.

The VM publishes only the allowlisted projections produced by
`cloud_publish.py`: report Markdown, sanitized deterministic data, ranked
catalysts, audited evidence/research, historical comparisons, agent/source
scores, and aggregate usage. Raw market data, prompts/model traces, logs,
SQLite, credentials, request IDs, and failure files are never uploaded.

Required runtime environment on the generator VM:

```text
GCP_PROJECT_ID=YOUR_PROJECT_ID
MARKET_BRIEF_GCS_BUCKET=YOUR_PRIVATE_BUCKET
MARKET_BRIEF_WEB_URL=https://YOUR_SERVICE_URL
```

Optional Gmail completion delivery uses `GMAIL_SMTP_USER`,
`GMAIL_APP_PASSWORD`, and `MARKET_BRIEF_EMAIL_TO`. Keep these only in the
root-owned `/etc/market-brief.env` file with mode `0600`.
