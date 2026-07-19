# Security Policy

**English** · [简体中文](SECURITY.zh-CN.md)

## Reporting a vulnerability

Please report security issues privately through this repository's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
channel rather than opening a public issue. Include reproduction steps and the
affected file paths.

Please do **not** include real API keys, tokens, or personal data in a report.

## Credential handling

- No credential value belongs in this repository. `.gitignore` blocks `.env`,
  `credentials.*`, `*.pem`, `*.key`, `*.p12`, and service-account JSON.
- Provider keys are read from the process environment or from a file **outside**
  the repository: `~/.config/market-brief/credentials.env` (mode `600`) on a
  workstation, or root-owned `/etc/market-brief.env` (mode `600`) on a Linux
  server.
- Scripts parse that file against a key-name allowlist instead of `source`-ing
  it, and refuse to read files that are group- or world-readable.
- `deploy/gcp/sync_credentials.sh` prints key **names** only, never values.
- If a key is ever committed or pasted anywhere, revoke and rotate it at the
  provider first. Rewriting Git history is not sufficient on its own.

## Network exposure

- `report_viewer.py` binds `127.0.0.1` by default and serves read-only content.
  It has no authentication and must not be bound to a public interface.
- Reach a remote viewer through an SSH tunnel or Google Cloud IAP TCP
  forwarding, not by opening a firewall port.
- The viewer sends `Content-Security-Policy: default-src 'none'`, escapes report
  text rather than rendering embedded HTML, and loads no external CDN.
- Do not publish `out/`, `data/`, `logs/`, or `state/` to the public internet.
  Those directories hold raw model output and market snapshots.

## Supported versions

Only the `main` branch is supported. Continuous integration runs on Python 3.11
and 3.12 and never calls a live model or market API.
