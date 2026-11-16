# Project progress / 项目进度

**Last updated / 最后更新：2026-11-16，Asia/Dubai（阿布扎比使用同一时区）。**

Read this file first when continuing. / 每次续接任务先读取本文件。

## Current state / 当前状态

- Project name: **iptv-QA**, release candidate **v0.1.0**. ECS verification is complete; local download and GitHub publishing are in progress. No public release is claimed yet.
- 源码已从运行部署中单独整理；OBS 默认关闭，可配置区域、Endpoint、桶、前缀、对象名、缓存头及重试；本地网页无需 OBS 即可提供订阅。
- Public source registry distinguishes configured inputs, untested candidates and reference tools. Private subscriptions and raw playlists are excluded.
- 中英文 README、设计、部署、来源、测试、网页播放器可行性文档均完成，并提供语言切换；`AGENTS.md` 指定每次先读本进度。
- Existing private production run remains independent. New project changes have not been installed over its running process.

## Implemented and verified / 已实现及验证

| Area / 事项 | Evidence / 证据 |
| --- | --- |
| Previous deployed video QA | 57 Linux tests and actual live HLS / audio-only fixtures passed |
| Public project unit tests | **62 Linux tests passed on ECS**; local Windows run passed with 9 Linux-only tests skipped |
| Optional OBS configuration | Region resolution, explicit endpoint precedence, retry/cache settings have regression tests |
| Local playlist dashboard | Local-only serving without OBS passed Linux HTTP integration |
| Bilingual entry and design | README and design language-switch links present |
| GitHub tooling | Isolated GitHub CLI verified on ECS; repository authentication/publishing not yet completed |
| Real-media checks | Full publication, zero-result retention and realtime HLS/audio-only checks passed |
| Publication audit | Source-only package, no detected secret patterns or broken local documentation links |

## Active tasks / 正在做

1. Download the ECS-verified source to the requested local folder, then initialize and publish from that checkout.
2. GitHub 新仓库需要 CLI 登录授权；现有连接不能直接新建仓库。设备授权已发起，等待用户完成。发布目标名称为 `iptv-QA`。
3. Finalize the release archive and record repository/release/CI evidence after publication.

## Radio incident / 广播问题

- Root cause confirmed: the old audio-tolerant policy accepted radio as usable TV. Old set1 had **495 audio-only first choices among 1579 channels**, not a universal cutoff at a specific channel number.
- OBS set1 was downloaded and SHA-256 matched the server's old published set1. Raw item 1094 was a movie entry; the receiver's displayed ordering may differ.
- 三条实际广播源现场重新探测，全部被当前正在运行的新视频策略以 `audio_only_rejected` 拒绝。
- The newly extracted public project also excludes explicit radio tags/names/groups, including radio with encoded cover video. TVB/music-TV exceptions have regression tests.
- **Operational distinction:** the existing full QA run is still in deep checks and has not replaced the old OBS files. No immediate cleanup or fresh full-run publication is claimed here. The public-project radio metadata change is isolated from that already-running process.

## Browser-player research / 网页播放器研究

Completed feasibility documents and primary-source review of hls.js, IPTVnator, neTV and one WebTV repository. Same public endpoint is feasible using separate authenticated frontend/media services and on-demand proxy/remux/transcode. “WebTV” is ambiguous and the proposed automatic-transcoding claims are not assumed. **No player code or deployment was performed.**

## Next session checklist / 下次继续

- Re-read this file and any ignored `.local-context.md` before reconnecting or publishing.
- Check the radio investigation evidence; do not assume the old published list already uses the new QA rules.
- Run the full Linux suite and real-media fixtures against the public project, then record actual counts here.
- Verify documentation links, excluded files and archive content before commit/push.
- Record the canonical repository URL, commit SHA, release tag, CI state and download location only after verification.

## Deferred work / 暂不开发

Browser TV player: import set1, channel selection, previous/next and random play, using the existing host/port. **Feasibility research only; do not implement or deploy a player in this task.** Review specific upstream projects rather than assuming a product named “WebTV” has every claimed proxy/transcoding capability. Cover browser codec support, CORS, HTTPS/mixed content, authentication, on-demand transcode cost and stream availability.

## Known boundaries / 已知边界

Video-only deep QA does not yet validate audio decoding, A/V synchronization, black/frozen content or a specific Apple TV app. The 5/12/30-second settings are heuristics. Unit tests are not proof of field playback accuracy. See the design document and roadmap.
