# Project progress / 项目进度

**Last updated / 最后更新：2026-11-16。** Read this file first when continuing. / 每次续接先读本文件。

Live progress: [main/PROGRESS.md](https://github.com/Nzy189/iptv-QA/blob/main/PROGRESS.md). Release archives preserve their build-time snapshot. / 最新状态以 main 为准，版本压缩包保留打包时快照。

## Publication / 发布状态

- **Published / 已发布：v0.1.0** — [Repository](https://github.com/Nzy189/iptv-QA) · [Release and downloads](https://github.com/Nzy189/iptv-QA/releases/tag/v0.1.0).
- Release target: `7cac47518367c59f685a6211431efe89ddd37c9a`. ZIP and SHA256SUMS were uploaded, downloaded again and verified; the checksum file was also checked on Linux.
- Public project has been verified independently of production deployments. Pushing this repository does not install changes into a running service.

## Latest work / 最近完成

- **Web TV player implemented and deployed / 网页电视播放器已实现并部署**：`tv.py` + `webtv/`（内置 hls.js 1.7.4）挂载进仪表盘同进程，`/tv/` 密码门禁。功能：频道列表/搜索/上一台/下一台/随机/遥控器式台号输入/全屏/手机布局；源选择栏一键加载本页发布的 Set1/Set2/Set3/Top3，也可粘贴任意 m3u 链接；频道失败自动按 Set1→Set2→Set3 换备用源，增强模式（随机按钮旁开关）10 秒无数据换套、全失败后随机跳台重试至可播（上限 15 次）。
- 同源 HLS 代理（清单/分片/密钥/音轨全部改写，保留每频道 UA/Referer，仅代理已加载清单中的地址）；浏览器解不了编码或"有声音无画面"时自动按需 FFmpeg 转码（H.264/AAC、封顶 720p/2Mbps、最多 2 路、闲置 90 秒停止、`-nostdin` 修复了 stdin EOF 导致的转码进程自杀）；纯音频频道明确提示。备用链路按订阅链接末段文件名识别三套对齐清单。
- 部署在生产面板 8088 端口（`/opt/iptv-checker`，密码经 `/etc/iptv-checker/tv.env`，不进仓库）；生产 `web.py` 已与公开版统一（单元文件显式传参，旧文件备份为 `web.py.before-tv`）。转码用 `/opt/iptv-checker/bin/ffmpeg`。
- Redesigned both homepages as product introductions: clear value proposition, feature benefits, direct Top3/Top5 subscription links, backup sets, reports, quick start and documentation.
- 首页提供可点击的示例输出。完整列表明确为 Top5，与 Top3 覆盖相同入选频道；原始未检测输入不作为精选文件展示。
- Public documentation now describes the product without an individual deployment location, hardware profile or incident diary. Owner-specific operational details stay in ignored `.local-context.md`.
- 示例公开输出链接经 HTTP 检查可访问；中英文首页均通过 GitHub Markdown 实际渲染检查和文档链接检查。这不代表重新完成了频道实播验证。
- Documentation-only product presentation update; detection rules and existing deployed services are unchanged. The immutable v0.1.0 archive remains its original release snapshot.

## Verified capabilities / 已实现与验证

| Area / 事项 | Evidence / 证据 |
| --- | --- |
| QA and radio exclusion | 62 Linux tests; real HLS/audio-only fixtures; encoded-cover radio exclusion |
| Publication | Top3/Top5 and sets, reconnect validation, zero-result retention, local dashboard |
| Optional OBS | Configurable region/endpoint, retry/cache behavior; no credentials required for local use |
| CI | [Run 43849545891](https://github.com/Nzy189/iptv-QA/actions/runs/43849545891) passed on Python 3.10, 3.12 and 3.14; real media on 3.12 |
| Web TV player | 79 tests pass on Windows and ECS (17 player tests: auth, proxy rewriting, registry, playlists API, transcode flow); real playback verified externally — 720p/1080p channels play, transcoded output is H.264/AAC, transcode process stays alive after `-nostdin` fix |
| Documentation | Paired README/design/deployment/source/testing/research pages; language switching |

## Continuation / 下次继续

1. Read this progress file; for a specific installation, read its ignored `.local-context.md` and check live status before making operational claims.
2. For runtime changes, read the design and deployment documents, run the relevant Linux/media tests, then update evidence. For documentation changes, run `python3 scripts/check_publication.py`; pure documentation pushes skip media CI.
3. Keep both homepages synchronized. Put benefits and useful outputs first; technical detail belongs in the linked guides. Do not expose private input configurations or credentials. Explicitly published demo download links are allowed.
4. Update this file after meaningful work. Record repository/release targets only after verification; never equate new source code with a completed production publication.

## Next priorities / 后续重点

- Independent audio/video validation, A/V synchronization and receiver compatibility.
- Longer checks for frequently watched Top1 sources and labelled playback comparisons.
- Black/freeze/silence flags and confidence-aware historical ranking.
- 播放器后续可选：会话跨服务重启保持（当前重启需重新输密码）；转码档位/并发按实测调整。原始可行性研究：[English](docs/WEB_PLAYER_FEASIBILITY.md) · [中文](docs/WEB_PLAYER_FEASIBILITY.zh-CN.md)。

## Capability boundary / 能力边界

Deep checks currently validate video only. Startup/stall measurements are server-side estimates; 5/12/30-second defaults are project heuristics. Tests do not establish a receiver playback-success percentage. See the design documents and roadmap.
