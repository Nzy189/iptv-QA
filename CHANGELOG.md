# Changelog / 变更记录

## Unreleased — web TV player / 网页电视播放器

- Added a password-gated web TV player at `/tv/` inside the dashboard service: channel list with search, previous/next/random switching, remote-style number entry, fullscreen and mobile layout.
- The server fetches configured playlists and proxies HLS playlists, segments, keys and audio tracks same-origin, honouring per-channel User-Agent/Referer options; only addresses discovered in loaded playlists are proxied.
- Playback uses vendored hls.js 1.7.4 (Apache-2.0) with native HLS fallback; no transcoding — browser-incompatible codecs and dead sources surface as an error with retry.
- Access password is supplied via `IPTV_TV_PASSWORD` (systemd `EnvironmentFile`), never stored in the repository.

中文：新增密码门禁的网页电视（`/tv/`）：频道列表与搜索、上一台/下一台/随机、遥控器式台号输入、全屏与手机布局。服务端代取订阅并同源代理 HLS（含密钥与音轨，带每频道 UA/Referer），仅代理已加载清单中的地址。播放内置 hls.js 1.7.4，不转码；不兼容编码或失效源显示错误并可重试。密码经 `IPTV_TV_PASSWORD` 环境文件提供，不进仓库。

## Unreleased — product documentation / 产品文档

- Redesigned bilingual homepages around benefits, subscription downloads and quick start.
- Added direct links to published Top3, full Top5, backup sets and reports.
- Removed individual deployment location, hardware and incident details from public documentation.

## 0.1.0 — initial public project / 首个公开版本

- Extracted the independent QA pipeline, source catalogue, quality observer, history and dashboard from the deployment.
- Made OBS optional and configurable; added local playlist routes and explicit radio exclusion.
- Added bilingual design, deployment, source and testing documentation, continuation progress, CI and publication audit.
- Validated through automated tests and real-media fixtures. Private credentials, input configurations and binary tools are not redistributed.

中文：整理独立 QA 项目；OBS 可选配置；本地订阅入口；广播排除；中英文文档、持续进度、测试和发布检查。实际发布状态以 PROGRESS 为准。
