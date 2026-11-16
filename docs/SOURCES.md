# Source inventory

[English](SOURCES.md) | [简体中文](SOURCES.zh-CN.md) · [Home](../README.md)

The machine-readable inventory is [sources/registry.json](../sources/registry.json). It records references, not a guarantee that any stream is playable. References reviewed on 2026-11-16.

| Input | Status | How it is used |
| --- | --- | --- |
| [zbds public playlist](https://live.zbds.top/tv/iptv4.m3u) | Configured direct input | Download, normalize and validate |
| [Tvlist-awesome](https://github.com/imDazui/Tvlist-awesome-m3u-m3u8) | Configured catalogue | Download archive; read playlist text without extracting/executing files |
| [iptv-org ordinary index](https://iptv-org.github.io/iptv/index.m3u) | Configured catalogue | Chinese/English routing; country and language subsets can duplicate this input |
| [iptv-org channel metadata](https://iptv-org.github.io/api/channels.json) | Configured metadata | IDs, countries and labels assist classification |
| [Free-TV/IPTV](https://github.com/Free-TV/IPTV) | Discovered candidate | Public `playlist.m3u8`; not enabled by default or stream-tested here |
| [CGTN official live](https://www.cgtn.com/tv) | Website candidate | Requires a suitable public playback adapter; not itself a stable M3U URL |

In the original catalogue snapshot, 224 repository files plus one ordinary global index produced 69,088 EXTINF records and 50,973 unique raw URLs. Language heuristics identified 31,976 Chinese candidate endpoints and 464 English candidate endpoints. These were **input candidates**, not verified TV channels; later routing and actual video tests reduce them substantially.

`catalog.playlist_urls` can add public lists. Additional direct subscriptions go in `sources`. Private/paid subscriptions belong only in the operator's ignored configuration. The package intentionally does not include a raw snapshot of private or aggregated playback URLs.

## Radio and content policy

The original implementation accepted audio-only media and accidentally included FM/radio in television results. The current project applies two gates: explicit station metadata/names/groups are excluded, and the TV media probe requires real video. A radio station with an encoded cover image is still a station and should not re-enter the TV list merely because it has a video track. TVB and music TV must not be mistaken for radio. Some labels remain ambiguous; media evidence and receiver feedback remain useful.

Chinese goes into the main result; English into a separate playlist. Other/unknown languages and NSFW are excluded by default. Metadata can be incomplete or stale, and inferred country/language is not speech recognition.

## Tools are not source pools

- [fanmingming/live](https://github.com/fanmingming/live): its current README focuses on logos, EPG and tools; not counted as a newly verified pool.
- [Guovin/iptv-api](https://github.com/Guovin/iptv-api): aggregation/checking tool; its tutorial requires user-supplied subscriptions.
- [zhimin-dev/iptv-checker](https://github.com/zhimin-dev/iptv-checker): reviewed as a checking implementation; not a playlist source or dependency.

A URL appearing in several repositories is still one upstream request. New source evaluation should report **new unique endpoints, newly playable channels and selected-source contribution**, rather than repository stars or raw list length. Upstream data/media terms are independent of this project's MIT software license.
