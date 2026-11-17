# Project progress / 项目进度

**Updated / 更新：2026-11-16。Read this first / 每次续接先读本文件。** Private installation details belong in ignored `.local-context.md`.

## Current work / 当前工作

The modular architecture is merged into `main` at `7b523c2`. The previous v0.1.0 release remains unchanged; the repository update does not automatically upgrade a production installation.

Review / 审阅：[PR #1](https://github.com/Nzy189/iptv-QA/pull/1) 已合并。Implementation tested at `3a7b104`; subsequent progress/documentation updates do not change runtime behavior.

- Implemented / 已实现：package `iptv_qa/` separates catalogue, detection, publishing, reporting and playback; shared parsing/I/O/locks are in `common`; HTTP hosting and scheduled orchestration are separate layers.
- Independent commands / 独立命令：`python3 -m iptv_qa {catalog,check,publish,report,dashboard,play,serve,run}`. Playback needs no detector/config/OBS; report regeneration needs no media tools; upload retries need no new QA run.
- Compatibility / 兼容：seven old root modules remain shims. Existing QA policy, playlist names, player URLs and frontend behaviors are preserved. `check` retains integrated optional publication.
- Playback / 放映：proxy, sessions/registry, transcoding and service are split; assets are packaged. Standalone player mode, adjustable transcode concurrency/idle limits and shutdown cleanup added. Combined or separate HTTP service templates are available.
- Documentation / 文档：paired architecture and playback guides; README presents the major modules; detection/deployment guides are targeted; research is marked historical and roadmap contradictions removed.
- Curated group / 精选分组：optional `international_news` config appends a curated M3U as one group (default title `International News`) to Top3 and the three sets, including the English Top3, never to Top5. Entries bypass QA validation; a missing file only warns and publication continues. Implemented, tested and deployed to the owner's private production host on 2026-11-16: the published Top3 and three sets now carry the 14-entry group, Top5 stays QA-only. / 已实现、测试并部署到私有生产主机：Top3 与三套已带 14 条精选分组，Top5 保持纯质检输出。

## Verification / 验证证据

- Baseline before refactor: 81 unit/HTTP tests; Windows skipped 9 Linux-only cases.
- Refactor: 91 unit/HTTP tests; Windows passed 82 and skipped the same 9 Linux-only cases. Added tests cover import boundaries, independent commands, locks, standalone HTTP routing and player cleanup.
- Linux compatibility: all 91 tests passed with Python 3.6.8 in an isolated source directory. FFmpeg 7.0.2 fixtures passed Top3/Top5 publication, zero-result retention, audio-only rejection, radio exclusion and two-connection publication.
- Wheel built successfully; isolated extraction validates legacy imports, player assets and standalone CLI without importing the detector for playback. This validates packaging, not real upstream playback quality.
- GitHub CI passed on Python 3.10, 3.12 and 3.14, including real media and wheel isolation on 3.12: [push run](https://github.com/Nzy189/iptv-QA/actions/runs/43902507773), [PR run](https://github.com/Nzy189/iptv-QA/actions/runs/43902676826). Local publication audit passed with zero detected secrets and zero broken document links.
- International news group (2026-11-16): 94 unit/HTTP tests on Windows — 84 passed, 10 Linux-only cases skipped (9 previous plus the new generate-sets append case). The real 14-entry `data/news-research/news-curated.m3u` was smoke-tested through `international_news()` + `append_group()`: all entries rewritten into one `International News` group. Publication audit: `PUBLICATION_AUDIT_OK files=98 secrets=0 broken_local_links=0`. Deployment verified the same day on the private host: its sets/upload services ran clean, and the public OBS objects were re-fetched and counted — 14 group entries in `iptv_top3.m3u` and each of the three sets, 0 in `iptv_ok.m3u`. The production checker is the pre-modular single file and received a minimal backport with a kept backup; the next successful full publication reproduces the group natively. / 精选分组已过 Windows 94 项测试、真实清单冒烟和发布审计，并已在私有主机部署验证：OBS 公网对象中 Top3 与三套各 14 条、Top5 为 0。
- Existing release/media evidence is retained in `docs/VALIDATION.json`; this branch's checks are recorded separately. No production services were replaced or restarted.

## Continue here / 后续接续

1. Read this file, then [architecture](docs/ARCHITECTURE.md) / [中文架构](docs/ARCHITECTURE.zh-CN.md). For private installations, read `.local-context.md` and check live state before operational claims.
2. New feature code belongs in its `iptv_qa/` module; keep root compatibility shims. QA rule changes require paired DESIGN updates; playback changes require paired PLAYBACK updates.
3. Run `python3 -m unittest discover -s tests -v`, real media/quality scripts on Linux, `python3 scripts/check_publication.py`, and wheel validation. Do not conflate test fixtures with receiver success rates.
4. Before upgrading production, use an isolated checkout. Select combined HTTP or split dashboard/player services; deploy the full package including assets. Never copy private sources, credentials or runtime data into public commits.

## Remaining product work / 后续能力

Separate audio/video QA, A/V synchronization, black/freeze/silence review, receiver comparisons and longer frequently watched Top1 checks remain plans. The browser player is already implemented; restart persistence and further compatibility improvements remain optional work. See [roadmap](ROADMAP.md).

## Local news shortlist research / 本地新闻精选研究（2026-11-16）

- Prepared ignored local M3U artifacts with 14 entries (10 video channels and 4 audio-only radio services), plus separate TV/radio lists. This is an exploratory shortlist, not a released feature or deployed subscription.
- 已生成 Git 忽略的本地14项精选及电视/广播分表（10个视频频道、4个纯广播）；属于研究产物，未发布、未部署。
- Evidence / 证据：`python data/news-research/probe.py`, `python data/news-research/extra.py`, `python data/news-research/export.py`; ffprobe identified audio and FFmpeg decoded four seconds of audio for each included entry. Durations measure probing, not receiver startup; long-term playback and production-network performance remain unverified.
- CNN FAST failed audio decoding after successful probing; tested Taiwan sources and RTHK TV32 did not pass. Excluded failed candidates. Audio-only radio must remain separate from video-required checker inputs. / CNN FAST、已测台湾源及港台电视32未通过全部检查，未纳入；广播不应送入要求视频的检测流程。
- The research itself changed no runtime code; the shortlist now feeds the optional `international_news` publication feature (see Current work), which was deployed on 2026-11-16. / 研究阶段未改运行代码；该精选清单现已接入可选的 `international_news` 发布功能（见上），并已部署。

## Diagnostic interpretation / 检测结果解释（2026-11-16）

- Read-only operational review confirmed that short-decode success and complete deep/reconnect success are different metrics; historical results from different validation policies must not be presented as a direct outage percentage. Publication safety can intentionally retain an older playlist and return a nonzero service exit. / 只读核查确认短检通过与完整深检复检通过是不同指标；不同检测策略的历史数量不能直接当成失效比例。发布保护可能主动保留旧订阅并返回非零服务退出码。
- Detailed installation evidence remains in ignored local context. No runtime, configuration, deployment or publication changes were made. / 具体部署证据保留在被忽略的本地记录，未修改运行代码、配置、部署或发布结果。

## Scheduling operation / 调度操作（2026-11-16）

- Applied the user-confirmed daily cadence to the existing private deployment timer; backed up its prior unit and verified the timer active and next execution with `systemctl list-timers`. Installation-specific times and paths remain in ignored local context. / 按用户确认将私有部署定时器改为每日执行，备份旧配置，使用 `systemctl list-timers` 验证已激活及下次执行时间；具体时间和路径保留在忽略的本地记录。
- Checker process, QA policy and published subscriptions were unchanged; repository example schedules remain examples. / 未启动检测任务、修改检测标准或更换订阅；仓库调度示例未变。

## Browser redirect fix / 浏览器重定向修复（2026-11-17）

- Problem / 问题：a redirected HLS playlist could load while its relative segment/key/audio/init URLs were resolved against the original request path, causing child HTTP 404s and failed browser startup despite successful FFmpeg playback. / HLS 清单跳转后能读取，但相对分片、密钥、音轨、初始化文件仍按原入口路径解析，导致子请求404，出现 FFmpeg 能播而网页无法起播。
- Fixed / 修复：upstream retrieval returns the final response URL and the playback proxy uses it for all playlist URI rewriting. Added HTTP regression coverage for multi-hop master redirects, media redirects, segments with query strings, keys, init files, audio playlists and User-Agent preservation. Paired playback guides explain the behavior.
- Verified / 验证：`python -m unittest discover -s tests -v` ran 95 tests on Windows (85 passed, 10 platform skips) and all 95 passed in an isolated Linux/Python 3.6.8 checkout. `python scripts/check_publication.py` passed. Minimal compatible fix deployed to the existing private web service after isolated checks and backup; actual authenticated HTTP playback routes returned segments that decoded successfully for three sampled channels. This verifies proxy delivery, not visual playback on the user's browser. / Windows 95项中85通过、10项平台跳过；隔离Linux环境95项全过；发布审计通过。备份后将最小修复部署到私有网页服务，三个样本均通过实际认证HTTP接口取得分片并解码；尚未验证用户浏览器实际画面。
- Scope / 范围：checker rules and publication contents were not changed. The detection HLS observer already uses the final response URL; sampled recent failures were startup timeout and deep media error, not this proxy path bug. / 未改检测标准或订阅内容；检测器HLS观察器已使用最终响应地址，样本近期淘汰原因为起播超时和深检媒体错误。
