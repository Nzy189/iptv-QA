<div align="center">

# iptv-QA

### Check sources. Publish playlists. Watch in your browser.

**Real video checks · Ranked subscriptions · Browser playback · Independent modules**

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml/badge.svg)](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Nzy189/iptv-QA)](https://github.com/Nzy189/iptv-QA/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[**Channel subscription**](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) · [**Download iptv-QA**](https://github.com/Nzy189/iptv-QA/releases/latest)

[Quick start](#quick-start) · [Playlists](#playlists-and-downloads) · [Documentation](#documentation) · [Project progress](PROGRESS.md)

</div>

iptv-QA collects, deduplicates and tests IPTV sources, then turns them into ranked channel subscriptions. It combines actual video decoding, live-playlist freshness checks and independent reconnection tests to help filter out broken, audio-only and unreliable candidates.

Run it on your own server, import the output into your M3U player or use built-in browser playback, and let scheduled jobs keep the results updated.

Tiered checking separates current A/V playback from long-term stability: Top3/Top5/sets prefer twice-verified sources and retain basic playable fallbacks; `iptv_stable.m3u` is stable-only. See [validation tiers](docs/DESIGN.md#playback-tiers).

Optional [channel management](docs/LIBRARY.md) provides one persistent library, Top5 playback, reversible quarantine, versioned publication and private encrypted backups.

## What's new in v0.2.0

| Improvement | What it means |
| --- | --- |
| **Hedged source racing** | Clicking a channel loads all of its lines at once (up to five) and plays whichever shows a picture first — channel switching waits for the fastest line, not a lucky one. |
| **No more slow-start false kills** | The deep startup budget rises from 12 to 30 seconds: slow-starting but stable channels are kept, and startup speed only affects ranking, never qualification. |
| **Unified channel library** | Channel identity separate from stream lines, versioned publication, reversible quarantine and encrypted backups, with up-to-five-line playback. |

## What it does

- Real video checks: decoded frames, media progression, HLS playlist freshness, and re-verification over a new connection at least 30 minutes later.
- Deduplication and channel merging, with lines ranked by startup, recent results and historical reliability.
- Ranked subscriptions: Top3, Top5, three single-source sets, a stable-only list and a separate English selection.
- Browser playback: channel search, hedged multi-line racing, backup fallback and on-demand H.264/AAC transcoding.
- Unattended operation: scheduling, per-host concurrency control, retry handling and publication guards.
- Reporting: dashboard, exported CSV/JSON history and optional Huawei OBS uploads with stable object names.

**Not included:** uninterrupted-playback guarantees, a bundled fixed channel library, EPG, or access to paid/DRM-protected sources. Playback depends on the source, network and receiver; see [capability boundaries](docs/DESIGN.md#known-limitations).

## Why iptv-QA?

| Benefit | What you get |
| --- | --- |
| **Check real video** | Decode actual frames and inspect media progression, rather than accepting an address solely because it responds. |
| **Switch channels faster** | Hedged racing loads several lines at once and keeps the first one to show a picture. |
| **Make channel choice easier** | Merge duplicate channel names and rank sources by startup, recent test results and historical reliability. |
| **Keep alternatives ready** | One channel catalog exports full, compact and single-source views, so the main route and backups are ready together. |
| **Curate your own extras** | Optionally append an operator-curated group, such as international news, to Top3 and the three sets without re-testing it. |
| **Keep television separate** | Filter explicit radio entries and audio-only TV candidates; put English channels in a separate subscription. |
| **Automate the routine** | Run unattended with scheduling, per-host concurrency control, retry handling and publication guards. |
| **See what changed** | Review source contributions, failures and historical results in the dashboard or exported reports. |
| **Choose where files live** | Keep everything local, or enable configurable Huawei OBS uploads with stable object names. |

## Playlists and downloads

### One catalog, several views

Every playlist file is a different view of one channel catalog: the main subscription carries all ranked lines, and the other views simply keep fewer of them per scenario. The built-in player offers three entries — combined channels (the main view, up to five ranked lines per channel with automatic racing), today's supplement and English channels.

Open a file below or copy its link into a player that supports M3U subscriptions.

| View | Lines per channel | Best for | File |
| --- | --- | --- | --- |
| **Main subscription** | Up to 5 | Everyday use: full backups and automatic line selection | [iptv_ok.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) |
| **Compact subscription** | Up to 3 | A shorter list for lightweight players | [iptv_top3.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_top3.m3u) |
| **Single-source compatibility** | 1 | Older devices or players that accept one line per channel | [Set 1](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set1.m3u) · [Set 2](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set2.m3u) · [Set 3](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set3.m3u) |
| **English channels** | Up to 3 | A separate English-language selection | [iptv_english.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_english.m3u) |
| **Detection report** | — | Latest published statistics and history | [iptv_summary.html](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_summary.html) |

The views cover the same selected channels and differ only in how many lines each keeps; the single-source sets are compatibility exports for older receivers and match the main subscription's first choice. The main subscription is the catalog's full view, not an untested raw-source dump. Sets reuse the last available source when a channel has too few backups.

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

**Requirements:** Linux, Python 3, FFmpeg and ffprobe. FFmpeg 7.x is the reference version. OBS and cloud storage are optional.

### Check your own playlist

Have an M3U subscription or file? Point iptv-QA at it and check only that:

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data --source /path/to/your.m3u
python3 -m iptv_qa check --config config.local.json
```

- `--source` accepts an `http(s)` URL or a local playlist file, and can be repeated for several subscriptions.
- `check` skips catalogue discovery and tests only the sources you configured.
- Expect the first results in roughly 30–60 minutes: every surviving candidate is watched for at least 30 seconds of real video, then re-verified over a new connection at least 30 minutes later. The wait is the quality gate, not overhead.

After the run, view the local results:

```bash
python3 -m iptv_qa dashboard --data-dir ./data --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/`. **OBS is off by default**; local checking, output files and the dashboard work without cloud storage.

### Run the full pipeline

Without `--source`, `run` starts from the default public catalogues (tens of thousands of candidate endpoints), discovers and checks everything, and publishes the ranked subscriptions — this is how the example subscriptions above are produced. A full run can take hours depending on source count:

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 -m iptv_qa run --config config.local.json
```

For scheduling and optional OBS uploads, follow the [deployment guide](docs/DEPLOYMENT.md).

### Watch without checking

Browser playback works without running QA — import any M3U:

```bash
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --port 8089
```

Open `http://127.0.0.1:8089/tv/`. FFmpeg is needed only for fallback transcoding. Source checkout uses the standard library; `python3 -m pip install .` also installs the CLI and complete player assets.

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
