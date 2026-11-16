# Project progress / 项目进度

**Updated / 更新：2026-11-16。Read this first / 每次续接先读本文件。** Private installation details belong in ignored `.local-context.md`.

## Current work / 当前工作

Branch: `refactor/modular-architecture`, based on `5d38c3c`. The previous v0.1.0 release remains unchanged. This branch is a modular source refactor, not an automatic production upgrade.

Review / 审阅：[PR #1](https://github.com/Nzy189/iptv-QA/pull/1). Implementation tested at `3a7b104`; subsequent progress/documentation updates do not change runtime behavior.

- Implemented / 已实现：package `iptv_qa/` separates catalogue, detection, publishing, reporting and playback; shared parsing/I/O/locks are in `common`; HTTP hosting and scheduled orchestration are separate layers.
- Independent commands / 独立命令：`python3 -m iptv_qa {catalog,check,publish,report,dashboard,play,serve,run}`. Playback needs no detector/config/OBS; report regeneration needs no media tools; upload retries need no new QA run.
- Compatibility / 兼容：seven old root modules remain shims. Existing QA policy, playlist names, player URLs and frontend behaviors are preserved. `check` retains integrated optional publication.
- Playback / 放映：proxy, sessions/registry, transcoding and service are split; assets are packaged. Standalone player mode, adjustable transcode concurrency/idle limits and shutdown cleanup added. Combined or separate HTTP service templates are available.
- Documentation / 文档：paired architecture and playback guides; README presents the major modules; detection/deployment guides are targeted; research is marked historical and roadmap contradictions removed.

## Verification / 验证证据

- Baseline before refactor: 81 unit/HTTP tests; Windows skipped 9 Linux-only cases.
- Refactor: 91 unit/HTTP tests; Windows passed 82 and skipped the same 9 Linux-only cases. Added tests cover import boundaries, independent commands, locks, standalone HTTP routing and player cleanup.
- Linux compatibility: all 91 tests passed with Python 3.6.8 in an isolated source directory. FFmpeg 7.0.2 fixtures passed Top3/Top5 publication, zero-result retention, audio-only rejection, radio exclusion and two-connection publication.
- Wheel built successfully; isolated extraction validates legacy imports, player assets and standalone CLI without importing the detector for playback. This validates packaging, not real upstream playback quality.
- GitHub CI passed on Python 3.10, 3.12 and 3.14, including real media and wheel isolation on 3.12: [push run](https://github.com/Nzy189/iptv-QA/actions/runs/43902507773), [PR run](https://github.com/Nzy189/iptv-QA/actions/runs/43902676826). Local publication audit passed with zero detected secrets and zero broken document links.
- Existing release/media evidence is retained in `docs/VALIDATION.json`; this branch's checks are recorded separately. No production services were replaced or restarted.

## Continue here / 后续接续

1. Read this file, then [architecture](docs/ARCHITECTURE.md) / [中文架构](docs/ARCHITECTURE.zh-CN.md). For private installations, read `.local-context.md` and check live state before operational claims.
2. New feature code belongs in its `iptv_qa/` module; keep root compatibility shims. QA rule changes require paired DESIGN updates; playback changes require paired PLAYBACK updates.
3. Run `python3 -m unittest discover -s tests -v`, real media/quality scripts on Linux, `python3 scripts/check_publication.py`, and wheel validation. Do not conflate test fixtures with receiver success rates.
4. Before upgrading production, use an isolated checkout. Select combined HTTP or split dashboard/player services; deploy the full package including assets. Never copy private sources, credentials or runtime data into public commits.

## Remaining product work / 后续能力

Separate audio/video QA, A/V synchronization, black/freeze/silence review, receiver comparisons and longer frequently watched Top1 checks remain plans. The browser player is already implemented; restart persistence and further compatibility improvements remain optional work. See [roadmap](ROADMAP.md).
