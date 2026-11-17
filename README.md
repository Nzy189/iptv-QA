<div align="center">

# iptv-QA

### Check sources. Publish playlists. Watch in your browser.

**Real video checks · Ranked subscriptions · Browser playback · Independent modules**

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml/badge.svg)](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Nzy189/iptv-QA)](https://github.com/Nzy189/iptv-QA/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[**Top3 playlist**](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_top3.m3u) · [**Full playlist · Top5**](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) · [**Download iptv-QA**](https://github.com/Nzy189/iptv-QA/releases/latest)

[Quick start](#quick-start) · [Playlists](#playlists-and-downloads) · [Documentation](#documentation) · [Project progress](PROGRESS.md)

</div>

iptv-QA collects, deduplicates and tests IPTV sources, then turns them into ranked channel subscriptions. It combines actual video decoding, live-playlist freshness checks and independent reconnection tests to help filter out broken, audio-only and unreliable candidates.

Run it on your own server, import the output into your M3U player or use built-in browser playback, and let scheduled jobs keep the results updated.

Tiered checking separates current A/V playback from long-term stability: Top3/Top5/sets prefer twice-verified sources and retain basic playable fallbacks; `iptv_stable.m3u` is stable-only. See [validation tiers](docs/DESIGN.md#playback-tiers).

## Why iptv-QA?

| Benefit | What you get |
| --- | --- |
| **Check real video** | Decode actual frames and inspect media progression, rather than accepting an address solely because it responds. |
| **Make channel choice easier** | Merge duplicate channel names and rank sources by startup, recent test results and historical reliability. |
| **Keep alternatives ready** | Top3, Top5 and three single-source sets give you a main route and practical backups. |
| **Curate your own extras** | Optionally append an operator-curated group, such as international news, to Top3 and the three sets without re-testing it. |
| **Keep television separate** | Filter explicit radio entries and audio-only TV candidates; put English channels in a separate subscription. |
| **Automate the routine** | Run unattended with scheduling, per-host concurrency control, retry handling and publication guards. |
| **See what changed** | Review source contributions, failures and historical results in the dashboard or exported reports. |
| **Choose where files live** | Keep everything local, or enable configurable Huawei OBS uploads with stable object names. |

## Playlists and downloads

### Example subscriptions

Open a file below or copy its link into a player that supports M3U subscriptions.

| Playlist | Best for | File |
| --- | --- | --- |
| **Top3** | A compact selection: up to three sources per channel | [iptv_top3.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_top3.m3u) |
| **Full playlist · Top5** | The complete selected channel lineup, with up to five sources per channel | [iptv_ok.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) |
| **Set 1 · Main** | One first-choice source per channel | [iptv_set1.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set1.m3u) |
| **Set 2 / Set 3 · Backups** | Alternative single-source channel sets | [Set 2](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set2.m3u) · [Set 3](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set3.m3u) |
| **English** | A separate English-language selection | [iptv_english.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_english.m3u) |
| **Detection report** | Latest published statistics and history | [iptv_summary.html](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_summary.html) |

**Top3 and Top5 cover the same selected channels.** They differ in the number of sources retained per channel. The full playlist is the ranked Top5 output, not an untested raw-source dump. Sets reuse the last available source when a channel has too few backups.

These links serve the last published snapshot and may lag behind the current QA rules while a new run is in progress. Check the report for publication time and status. The source code does not bundle a fixed channel library; your deployment can use different sources and output addresses.

<details>
<summary>More output files and integration details</summary>

- [CSV history](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_history.csv) and [JSON history](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_history.json) support analysis and integrations.
- Local Top5 is named `iptv_top5.m3u`; the default public filename is `iptv_ok.m3u`.
- Without OBS, English output lives at `data/english/published/iptv_top3.m3u`; the dashboard serves it as `iptv_english.m3u`.
- Chinese and English outputs are published in separate stages. Details: [deployment guide](docs/DEPLOYMENT.md).

</details>

## How it works

**Collect → Deduplicate → Probe → Decode → Observe → Reconnect → Rank → Publish**

Shortlisted sources are observed for at least 30 seconds of both video progression and wall time, then checked again through a new connection after at least 30 minutes. HLS checks look for ongoing playlist updates. Candidates passing both deep checks form the stable tier. In tiered mode, current basic A/V passes can remain as lower-priority fallbacks; strict mode requires both deep checks for all published entries.

Publication guards retain the previous result when a run produces zero usable channels or an unexpected drop. See [the design](docs/DESIGN.md) for the full rules and tuning options.

## Use the modules you need

**Catalogue → Quality checks → Publication**, with independent **reporting** and **browser playback**. Watch an external M3U without running QA, check sources without a player, and choose whether OBS is involved. See [architecture and source layout](docs/ARCHITECTURE.md).

| Your task | Command |
| --- | --- |
| Discover candidates / check sources | `catalog` / `check` |
| Upload existing results / rebuild reports | `publish` / `report` |
| Reports only / playback only | `dashboard` / `play` |
| Combined HTTP service / scheduled full workflow | `serve` / `run` |

The player includes channel search, number entry, random switching, automatic backup fallback and on-demand H.264/AAC transcoding. See the [playback guide](docs/PLAYBACK.md).

## Quick start

**Checking requirements:** Linux, Python 3, FFmpeg and ffprobe. FFmpeg 7.x is the reference version.

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 -m iptv_qa run --config config.local.json
```

After the first run, view the local results:

```bash
python3 -m iptv_qa dashboard --data-dir ./data --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/`. **OBS is off by default**; local checking, output files and the dashboard work without cloud storage. For your own sources, scheduling or optional uploads, follow the [deployment guide](docs/DEPLOYMENT.md).

Start browser playback without running detection:

```bash
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --port 8089
```

Open `http://127.0.0.1:8089/tv/` and import your M3U. FFmpeg is needed only for fallback transcoding. Source checkout uses the standard library; `python3 -m pip install .` also installs the CLI and complete player assets.

## Documentation

| Explore | English | 中文 |
| --- | --- | --- |
| Design, ranking and tuning | [Design](docs/DESIGN.md) | [设计思路](docs/DESIGN.zh-CN.md) |
| Installation, scheduling and OBS | [Deployment](docs/DEPLOYMENT.md) | [部署配置](docs/DEPLOYMENT.zh-CN.md) |
| Source discovery and inventory | [Sources](docs/SOURCES.md) | [来源说明](docs/SOURCES.zh-CN.md) |
| Tests and reproducible validation | [Testing](docs/TESTING.md) | [测试说明](docs/TESTING.zh-CN.md) |
| Architecture and module boundaries | [Architecture](docs/ARCHITECTURE.md) | [架构说明](docs/ARCHITECTURE.zh-CN.md) |
| Browser playback and transcoding | [Playback](docs/PLAYBACK.md) | [放映指南](docs/PLAYBACK.zh-CN.md) |

[Project progress](PROGRESS.md) · [Roadmap](ROADMAP.md) · [Security](SECURITY.md) · [Source registry](sources/registry.json)

iptv-QA provides screening, publishing, reporting and browser playback. Results depend on the source, network and receiver; it is not a guarantee of uninterrupted playback. Detailed capability boundaries are in the [design documentation](docs/DESIGN.md#known-limitations). Software is [MIT licensed](LICENSE); upstream lists and streams retain their own terms.
