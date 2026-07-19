# 安全策略

[English](SECURITY.md) · **简体中文**

## 报告漏洞

请通过本仓库的 GitHub
[私密漏洞报告](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
渠道提交安全问题，不要直接开 public issue。请附上复现步骤与受影响的文件路径。

请**不要**在报告中包含真实的 API Key、令牌或个人数据。

## 凭据处理

- 仓库中不允许出现任何凭据的真实值。`.gitignore` 已屏蔽 `.env`、`credentials.*`、
  `*.pem`、`*.key`、`*.p12` 与 service-account JSON。
- 供应商密钥只从进程环境或**仓库之外**的文件读取：工作站上的
  `~/.config/market-brief/credentials.env`（权限 `600`），或 Linux 服务器上由 root
  拥有的 `/etc/market-brief.env`（权限 `600`）。
- 脚本按键名白名单解析该文件，不会 `source` 它，并拒绝读取 group 或 other 可读的
  文件。
- `deploy/gcp/sync_credentials.sh` 只打印键**名**，绝不打印值。
- 一旦密钥被提交或粘贴到任何地方，请先到供应商处吊销并轮换该密钥。仅仅重写 Git
  历史并不足够。

## 网络暴露面

- `report_viewer.py` 默认绑定 `127.0.0.1`，只提供只读内容。它没有认证机制，不得
  绑定到公网接口。
- 远程访问 viewer 请使用 SSH 隧道或 Google Cloud IAP TCP 转发，不要开放防火墙端口。
- viewer 发送 `Content-Security-Policy: default-src 'none'`，对报告文本做转义而非
  渲染内嵌 HTML，且不加载任何外部 CDN。
- 不要把 `out/`、`data/`、`logs/`、`state/` 发布到公网，这些目录包含原始模型输出与
  市场快照。

## 支持范围

仅支持 `main` 分支。CI 在 Python 3.11 与 3.12 上运行，且不会调用任何真实模型或行情
API。
