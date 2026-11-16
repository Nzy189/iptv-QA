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

Automated validation runs on Linux with Python 3.10, 3.12 and 3.14 in GitHub Actions. Real-media fixtures run on Python 3.12, and compatibility checks also cover Python 3.6.8 with FFmpeg 7.0.2. Use maintained OS/Python releases for new deployments. See [validation evidence](VALIDATION.json) for the verified run.

Before public-project extraction, the deployed checker passed 57 Linux regression tests. Public-project changes add tests for optional OBS configuration, local playlist serving and radio classification. See [PROGRESS](../PROGRESS.md) for the actual most recent test count and release verification rather than treating this historical count as current.

## Regression coverage

Regression tests cover audio-only rejection, explicit radio entries even when they contain encoded video, valid TV-name exceptions, stalled or stale HLS, reconnect failure, publication guards and local-only operation. This tests policy behavior with controlled inputs; it is not a random sample of public channels or a playback-accuracy benchmark.

## Limits of verification

Unit tests show that rules are implemented consistently. They cannot establish receiver compatibility, peak-hour reliability, correct channel content or a failure-rate percentage. Future calibration should label receiver success/failure for frequently watched channels and measure false accepts as well as false rejects. Keep operator URLs and tokens out of all public evidence.
