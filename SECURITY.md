# Security Policy / 安全策略

## Reporting a vulnerability / 报告漏洞

**EN** — Please report security issues privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, rather than opening a public issue. Include reproduction
steps and the affected file paths. Please do not include real API keys,
tokens, or personal data in a report.

**中文** — 请通过本仓库的 GitHub「私密漏洞报告」渠道提交安全问题，不要直接开
public issue。请附上复现步骤与受影响的文件路径，并且**不要**在报告中包含真实
API Key、令牌或个人数据。

## Credential handling / 凭据处理

**EN**

- No credential value belongs in this repository. `.gitignore` blocks `.env`,
  `credentials.*`, `*.pem`, `*.key`, `*.p12`, and service-account JSON.
- Provider keys are read from the process environment or from a file **outside**
  the repository, such as `~/.config/market-brief/credentials.env` (mode `600`)
  on a workstation, or root-owned `/etc/market-brief.env` (mode `600`) on a
  Linux server.
- Scripts parse that file against a key-name allowlist instead of `source`-ing
  it, and refuse to read files that are group- or world-readable.
- `deploy/gcp/sync_credentials.sh` prints key **names** only, never values.
- If a key is ever committed or pasted anywhere, revoke and rotate it at the
  provider first. Rewriting Git history is not sufficient on its own.

**中文**

- 仓库中不允许出现任何凭据的真实值；`.gitignore` 已屏蔽 `.env`、`credentials.*`、
  `*.pem`、`*.key`、`*.p12` 和 service-account JSON。
- 供应商密钥只从进程环境或**仓库之外**的文件读取，例如工作站上的
  `~/.config/market-brief/credentials.env`（权限 `600`），或 Linux 服务器上
  由 root 拥有的 `/etc/market-brief.env`（权限 `600`）。
- 脚本按键名白名单解析该文件，不会 `source` 它，并拒绝 group/other 可读的权限。
- `deploy/gcp/sync_credentials.sh` 只打印键名，绝不打印值。
- 一旦密钥被提交或粘贴到任何地方，请先到供应商处吊销并轮换；仅重写 Git 历史并
  不足够。

## Network exposure / 网络暴露面

**EN**

- `report_viewer.py` binds `127.0.0.1` by default and serves read-only content.
  It has no authentication and must not be bound to a public interface.
- Reach a remote viewer through an SSH tunnel or Google Cloud IAP TCP
  forwarding, not by opening a firewall port.
- The viewer sends `Content-Security-Policy: default-src 'none'` and escapes
  report text rather than rendering embedded HTML. It loads no external CDN.
- Do not publish `out/`, `data/`, `logs/`, or `state/` to the public internet.
  Those directories hold raw model output and market snapshots.

**中文**

- `report_viewer.py` 默认绑定 `127.0.0.1`，只提供只读内容；它没有认证机制，
  不得绑定到公网接口。
- 远程访问 viewer 请使用 SSH 隧道或 Google Cloud IAP TCP 转发，不要开放防火墙端口。
- viewer 发送 `Content-Security-Policy: default-src 'none'`，对报告文本做转义而
  非渲染内嵌 HTML，且不加载任何外部 CDN。
- 不要把 `out/`、`data/`、`logs/`、`state/` 发布到公网，这些目录包含原始模型输出
  与市场快照。

## Supported versions / 支持范围

**EN** — Only the `main` branch is supported. Continuous integration runs on
Python 3.11 and 3.12 and never calls a live model or market API.

**中文** — 仅支持 `main` 分支。CI 在 Python 3.11 与 3.12 上运行，且不会调用任何
真实模型或行情 API。
