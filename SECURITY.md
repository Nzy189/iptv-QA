# Security / 安全说明

Keep subscriptions, AK/SK, server credentials, database files and raw check logs outside Git. Use the ignored local configuration or `/etc/iptv-qa`, with restricted permissions. The release package includes no personal credentials, server IP, bucket or generated subscriptions.

The checker makes network requests to configured sources and their media references. Treat untrusted subscriptions as untrusted network input; do not expose a remote configuration/upload API or run arbitrary code from source repositories. Literal private/multicast addresses can be skipped, but this is not a complete SSRF boundary against redirects or DNS changes.

The dashboard is read-only and defaults to localhost. It has no built-in login. An operator who exposes playlist routes may expose the stream URLs inside them; use appropriate authentication and HTTPS at the reverse proxy. Config/env/database/JSONL routes are not served. OBS credentials are environment-only; the application never modifies bucket ACLs.

中文：私人订阅、密钥、数据库和原始日志不得进入 Git；使用被忽略的本地配置或受限的系统配置目录。来源地址是网络输入，内网字面地址过滤不是完整 SSRF 防线。网页只读，默认绑定本机，没有内置登录；公开订阅可能公开其内部地址，应由操作者配置反向代理鉴权及 HTTPS。不要在公开 Issue 中粘贴真实令牌。
