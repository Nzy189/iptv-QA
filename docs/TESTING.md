# Testing and evidence

[English](TESTING.md) | [简体中文](TESTING.zh-CN.md) · [Home](../README.md)

## Reproduce locally on Linux

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_media.py --bin-dir /usr/bin
python3 scripts/validate_quality.py --bin-dir /usr/bin
python3 scripts/check_publication.py
```

Use `--bin-dir DIRECTORY` or `IPTV_FFMPEG_DIR` for static binaries. Core tests use mocks, local HTTP servers, SQLite and subprocesses. They do not download arbitrary public subscriptions or contact OBS. The real-media tests generate their own small video/audio samples and a realtime HLS producer; they do not rely on a public TV station staying online.

`validate_media.py` verifies real decoding, early EOF, short-file rejection, complete Top3/Top5 publication and retention of the previous version after zero results. `validate_quality.py` verifies audio-only exclusion, real video frames, wall-clock observation, reconnecting twice, HLS advancement and successful end-to-end publication. Its test configuration shortens the observation/interval; production defaults remain 30 seconds and 1800 seconds.

## Test environment

Reference location: **Abu Dhabi, United Arab Emirates**. Deployment case: 4 vCPUs, approximately 15 GiB RAM, Linux/CentOS 7, Python 3.6.8, FFmpeg/ffprobe 7.0.2. New installations should not adopt an obsolete OS just to reproduce the case. Modern Python versions are covered by the included GitHub Actions matrix; a configured workflow is not evidence that a particular remote CI run has already passed.

Before public-project extraction, the deployed checker passed 57 Linux regression tests. Public-project changes add tests for optional OBS configuration, local playlist serving and radio classification. See [PROGRESS](../PROGRESS.md) for the actual most recent test count and release verification rather than treating this historical count as current.

## Field observations and interpretation

An intentionally selected sample of 16 former Top1 entries included known audio-only candidates and multiple codecs. Four returned audio only and one had a media-reading error; four selected video sources passed a 30-second observation after correcting the buffer-gap heuristic. Two HLS sources were separately observed advancing their playlists. This was a diagnostic sample, **not a random accuracy benchmark**.

The original published set1 contained 495 audio-only first choices among 1579 channels. The server file's raw position 1094 did not match the user's displayed radio boundary; client grouping/order can differ. The defect was media policy accepting audio, not a meaningful numeric channel cutoff. A currently running new checker and a previously published file are separate states; do not claim the old OBS file has been fixed just because new code exists.

## Limits of verification

Unit tests show that rules are implemented consistently. They cannot establish receiver compatibility, peak-hour reliability, correct channel content or a failure-rate percentage. Future calibration should label receiver success/failure for frequently watched channels and measure false accepts as well as false rejects. Keep operator URLs and tokens out of all public evidence.
