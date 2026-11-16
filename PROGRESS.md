# Project progress / 项目进度

**Last updated / 最后更新：2026-11-16，Asia/Dubai（阿布扎比使用同一时区）。**

Read this file first when continuing. / 每次续接任务先读取本文件。

Live progress: [main/PROGRESS.md](https://github.com/Nzy189/iptv-QA/blob/main/PROGRESS.md). Release archives retain a build-time snapshot. / 最新状态以 main 为准，版本压缩包保留打包时的进度快照。

## Current state / 当前状态

- **Published / 已发布：iptv-QA v0.1.0**. Public repository: **https://github.com/Nzy189/iptv-QA**. Release: **https://github.com/Nzy189/iptv-QA/releases/tag/v0.1.0**. Published 2026-11-16 **14:33 Abu Dhabi**. ECS tests, local handoff, GitHub CI and release download verification have passed.
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
| GitHub repository | Public Nzy189/iptv-QA created; main branch pushed from the local checkout |
| GitHub CI | **Run [43849545891](https://github.com/Nzy189/iptv-QA/actions/runs/43849545891) passed** on Python 3.10, 3.12 and 3.14, including real-media tests on 3.12; tested code/workflow commit `dcdc7b3ec221b86501f728d836192b25b18a467c` |
| Real-media checks | Full publication, zero-result retention and realtime HLS/audio-only checks passed |
| Publication audit | Source-only package, no detected secret patterns or broken local documentation links |
| Release | Public, non-draft `v0.1.0`; target `7cac47518367c59f685a6211431efe89ddd37c9a`; source ZIP and SHA256SUMS uploaded and downloaded again with identical SHA-256 |

## Publication completed / 发布已完成

1. **Local handoff complete:** ECS source ZIP SHA-256 verified after download; local checkout and initial commit created. `dist/iptv-QA-v0.1.0.zip` is built only from tracked public files. The operator-specific local path is in ignored `.local-context.md`.
2. GitHub 授权、公开仓库和完整 CI 均完成；进度及其他纯文档更新不重复触发媒体测试。
3. `v0.1.0` 已公开发布，源码包和 SHA256SUMS 已上传并回下载校验一致。发布目标提交记录在上表；main 的进度文档会继续更新，版本包保持打包时快照。
4. **Next operational follow-up / 下一项运维跟进：** check completion/publication of the separately running production QA and verify that the old audio-containing subscriptions have actually been replaced. Do not infer this from the GitHub release.

## Radio incident / 广播问题

- Root cause confirmed: the old audio-tolerant policy accepted radio as usable TV. Old set1 had **495 audio-only first choices among 1579 channels**, not a universal cutoff at a specific channel number.
- OBS set1 was downloaded and SHA-256 matched the server's old published set1. Raw item 1094 was a movie entry; the receiver's displayed ordering may differ.
- 三条实际广播源现场重新探测，全部被当前正在运行的新视频策略以 `audio_only_rejected` 拒绝。
- The newly extracted public project also excludes explicit radio tags/names/groups, including radio with encoded cover video. TVB/music-TV exceptions have regression tests.
- **Operational distinction:** last checked 2026-11-16 14:32 Abu Dhabi: the existing full QA run was in `deep1` (887/1479, 174 passed) and had not replaced the old OBS files. Re-read live status before drawing a current conclusion. The public-project radio metadata change is isolated from that already-running process.

## Browser-player research / 网页播放器研究

Completed feasibility documents and primary-source review of hls.js, IPTVnator, neTV and one WebTV repository. Same public endpoint is feasible using separate authenticated frontend/media services and on-demand proxy/remux/transcode. “WebTV” is ambiguous and the proposed automatic-transcoding claims are not assumed. **No player code or deployment was performed.**

## Next session checklist / 下次继续

- Re-read this file and any ignored `.local-context.md` before reconnecting or publishing.
- Check the radio investigation evidence; do not assume the old published list already uses the new QA rules.
- For detection/runtime changes, run the Linux suite and relevant real-media fixtures, then update evidence. For documentation-only changes, run the publication/link audit; CI deliberately skips pure documentation updates.
- Verify documentation links, excluded files and archive content before commit/push.
- Keep repository/release URLs and verified test evidence above accurate; record new release targets only after verification.

## Deferred work / 暂不开发

Browser TV player: import set1, channel selection, previous/next and random play, using the existing host/port. **Feasibility research only; do not implement or deploy a player in this task.** Review specific upstream projects rather than assuming a product named “WebTV” has every claimed proxy/transcoding capability. Cover browser codec support, CORS, HTTPS/mixed content, authentication, on-demand transcode cost and stream availability.

## Known boundaries / 已知边界

Video-only deep QA does not yet validate audio decoding, A/V synchronization, black/frozen content or a specific Apple TV app. The 5/12/30-second settings are heuristics. Unit tests are not proof of field playback accuracy. See the design document and roadmap.
