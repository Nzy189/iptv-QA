# iptv-QA

[English](README.md) | [简体中文](README.zh-CN.md)

[Progress / 进度](PROGRESS.md) · [Browser-player research / 网页播放器研究](docs/WEB_PLAYER_FEASIBILITY.md)

IPTV stream screening with real video decoding, repeated connection checks, ranked Top3/Top5 playlists, a history dashboard, and **optional Huawei OBS publishing**.

**Test location: Abu Dhabi, United Arab Emirates.** The deployment case uses 4 vCPUs, about 15 GiB RAM, Linux, Python 3.6.8 and FFmpeg 7.0.2. These observations describe one network vantage point; they do not establish availability on every ISP or Apple TV app. New installations should use a maintained Linux distribution and Python release.

## What it does

- Downloads subscriptions and public catalogues; deduplicates by URL and request headers.
- Routes Chinese channels into the main result and English channels into a separate playlist. Other, unknown-language and NSFW entries are excluded by the default policy.
- Probes media metadata, requires real video frames, and rejects audio-only TV entries and attached cover images.
- Short-tests candidates, then observes shortlisted video for at least 30 seconds of both media progression and wall time. Long HLS segments extend the observation window.
- Checks HLS playlist advancement and reconnects after at least 30 minutes. Only candidates passing both deep checks can be published.
- Ranks by startup category, retry status, historical reliability, startup-time bucket, resolution and total test duration; prefers different hostnames for backups.
- Generates reports locally. OBS is disabled by default and never required for checking or the local dashboard.
- Runs independently on a server, with an example Monday/Wednesday/Sunday schedule at **04:00 Abu Dhabi / 00:00 UTC / 08:00 Beijing**.

## Quick start

Linux is required for production locks and atomic symlink publication. Install Python and FFmpeg using your OS package manager, then:

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 iptv_checker.py --config config.local.json --check-config
python3 daily.py --config config.local.json
```

The full run can take hours. It downloads public candidate data, processes Chinese first, then English. To check only the subscriptions already specified in a configuration, use `iptv_checker.py` instead of `daily.py`. A diagnostic `--limit 20` run records samples but never publishes or updates reliability history.

After a run has created reports:

```bash
python3 web.py --data-dir ./data --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/`. Without `--public-base`, the dashboard serves the six explicitly allowed local playlists. With `--public-base https://YOUR_PUBLIC_HOST/PREFIX`, it redirects playlist requests there. The dashboard currently has a Chinese UI; the project documentation is bilingual.

## Outputs

| File | Purpose |
| --- | --- |
| `iptv_ok.m3u` | Main Top5, up to five validated sources per channel |
| `iptv_top3.m3u` | The first three sources from each channel's Top5 |
| `iptv_set1.m3u` | One source per channel, rank 1 |
| `iptv_set2.m3u` | One source per channel, rank 2 |
| `iptv_set3.m3u` | One source per channel, rank 3 |
| `iptv_english.m3u` | English channels, up to three sources per channel |
| `iptv_summary.html` | Latest statistics and history |
| `iptv_history.csv` | Spreadsheet-friendly historical summaries |
| `iptv_history.json` | Machine-readable historical summaries |

Sets reuse the last available source if a channel has too few backups. Chinese files are published before English completes; the nine remote objects are not one atomic transaction. Internal `iptv_top5.m3u` maps to the public `iptv_ok.m3u`. With OBS disabled, English is at `data/english/published/iptv_top3.m3u`, exposed as `iptv_english.m3u` by the dashboard.

## Configure sources and OBS

Edit the generated **private** `config.local.json`. The public [source registry](sources/registry.json) records working inputs, untested candidates and tools separately; no personal subscription, server address, bucket name or credentials are included.

OBS settings include `enabled`, `region`, `endpoint`, `bucket`, `prefix`, `object_names`, `playlist_files`, `publish_sets`, `upload_report`, `upload_retries`, `cache_control`, and `public_base_url`. Leave it disabled for local-only operation. AK/SK are read only from environment variables. See [deployment](docs/DEPLOYMENT.md#optional-obs-publishing).

## Documentation

| Topic | English | 中文 |
| --- | --- | --- |
| Design and limitations | [Design](docs/DESIGN.md) | [设计思路](docs/DESIGN.zh-CN.md) |
| Server, schedule and OBS | [Deployment](docs/DEPLOYMENT.md) | [部署配置](docs/DEPLOYMENT.zh-CN.md) |
| Public source inventory | [Sources](docs/SOURCES.md) | [来源说明](docs/SOURCES.zh-CN.md) |
| Reproducible validation | [Testing](docs/TESTING.md) | [测试说明](docs/TESTING.zh-CN.md) |

## Validation and scope

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_media.py --bin-dir /usr/bin
python3 scripts/validate_quality.py --bin-dir /usr/bin
python3 scripts/check_publication.py
```

These tests validate implementation behavior, not the percentage of streams that will work in your player. First-frame timing is an FFmpeg progress-sample upper bound, not Apple TV screen startup. Deep checks currently validate video only: audio decoding, A/V synchronization, visual black/freeze detection, content identity and receiver compatibility remain future work. The 5/12/30-second defaults are project heuristics, not industry standards. See [design limitations](docs/DESIGN.md#known-limitations).

MIT-licensed software. Upstream lists and streams retain their own terms; this repository contains source references and configuration, not a redistributed dump of subscriptions or media. See [security notes](SECURITY.md) and [roadmap](ROADMAP.md).
