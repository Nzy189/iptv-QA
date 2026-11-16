# Changelog / 变更记录

## Unreleased — web TV player / 网页电视播放器

- Added a password-gated web TV player at `/tv/` inside the dashboard service: channel list with search, previous/next/random switching, remote-style number entry, fullscreen and mobile layout.
- The source bar offers the published subscriptions (Set1/Set2/Set3/Top3) as one-click presets next to custom m3u URLs; failed channels automatically walk the aligned backup sets Set1 → Set2 → Set3, and an enhanced mode keeps zapping to random channels (10-second no-data timeout, up to 15 tries) until something plays.
- The server fetches configured playlists and proxies HLS playlists, segments, keys and audio tracks same-origin, honouring per-channel User-Agent/Referer options; only addresses discovered in loaded playlists are proxied.
- Playback uses vendored hls.js 1.7.4 (Apache-2.0) with native HLS fallback; browser-incompatible codecs and silent-video streams fall back to on-demand FFmpeg transcoding (H.264/AAC, 720p/2 Mbps cap, 2 concurrent, 90s idle stop), and audio-only channels show an explicit notice.
- Access password is supplied via `IPTV_TV_PASSWORD` (systemd `EnvironmentFile`), never stored in the repository.

中文：新增密码门禁的网页电视（`/tv/`）：频道列表与搜索、上一台/下一台/随机、遥控器式台号输入、全屏与手机布局；预设一键加载已发布订阅，也可粘贴任意 m3u。频道失败自动按 Set1→Set2→Set3 换备用源，增强模式 10 秒无数据即换套、全失败后随机跳台重试（最多 15 次）直到可播。服务端代取订阅并同源代理 HLS（含密钥与音轨，带每频道 UA/Referer），仅代理已加载清单中的地址。播放内置 hls.js 1.7.4，编码不兼容或有声无画自动按需 FFmpeg 转码（720p/2Mbps、2 路上限、90 秒闲置停），纯音频频道明确提示。密码经 `IPTV_TV_PASSWORD` 环境文件提供，不进仓库。

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
