# Deployment

[English](DEPLOYMENT.md) | [简体中文](DEPLOYMENT.zh-CN.md) · [Home](../README.md)

## Source checkout and prerequisites

Use Linux, Python 3 and FFmpeg/ffprobe. FFmpeg 7.x is the reference version; the video pipeline needs `-fps_mode` support. Core checking uses only Python's standard library. Python 3.6.8 was tested for compatibility with the original server; maintained Python/Linux versions are recommended for new installations.

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 iptv_checker.py --config config.local.json --check-config
python3 daily.py --config config.local.json
```

`configure.py --source URL` can replace the default direct subscription; repeat it for multiple subscriptions. The catalogue source is still appended for the full pipeline. Edit `ffmpeg` and `ffprobe` paths if using static binaries. `catalog.archive_url`, `catalog.metadata_url` and `catalog.playlist_urls` control discovery. Add the Free-TV candidate to `catalog.playlist_urls` only when intentionally evaluating it.

## Optional OBS publishing

OBS is disabled in the example. Install the isolated optional adapter only if needed:

```bash
python3 -m pip install --target ./obs-sdk -r requirements-obs.txt
export PYTHONPATH="$PWD/obs-sdk${PYTHONPATH:+:$PYTHONPATH}"
```

Configure the private JSON, for example:

```json
{
  "obs": {
    "enabled": true,
    "region": "me-east-1",
    "endpoint": "",
    "bucket": "YOUR_BUCKET_NAME",
    "prefix": "iptv",
    "object_names": {"iptv_top5.m3u": "iptv_ok.m3u"},
    "publish_sets": true,
    "upload_report": false,
    "upload_retries": 3,
    "cache_control": "max-age=300",
    "public_base_url": ""
  }
}
```

An explicit HTTPS endpoint takes precedence; otherwise the adapter derives `https://obs.REGION.myhuaweicloud.com`. Empty `prefix` writes at bucket root. `playlist_files` optionally restricts upload filenames; the English workflow selects Top3 and maps it to `iptv_english.m3u`. `object_names` values must be simple filenames. `public_base_url` overrides the URL base used in generated HTML; standard navigation assumes the documented filenames, so custom renames should be linked directly by the operator.

Keep credentials outside the checkout:

```bash
sudo install -d -m 0750 /etc/iptv-qa
sudo install -m 0600 config/obs.env.example /etc/iptv-qa/obs.env
# Edit the file locally: OBS_ACCESS_KEY_ID / OBS_SECRET_ACCESS_KEY
# Optional temporary credentials: OBS_SECURITY_TOKEN
```

The systemd service reads that environment file. For an interactive run, export these variables in the shell without placing values in command history. The adapter does not create a bucket, grant public access, or change ACLs. Configure the bucket's intended read access separately. TLS certificate validation uses certifi. Uploading nine objects is sequential; partial upload failure is reported and can be retried with `--upload-only`.

## systemd deployment

Do not enable a second checker against an existing production data directory. The public project's default directory names are independent of the original private deployment.

```bash
sudo useradd --system --home-dir /var/lib/iptv-qa --shell /sbin/nologin iptv-qa
sudo install -d -m 0755 /opt/iptv-QA
# Place this checkout's source files in /opt/iptv-QA (exclude .git and private config).
sudo install -d -o iptv-qa -g iptv-qa -m 0750 /var/lib/iptv-qa
sudo install -d -o root -g iptv-qa -m 0750 /etc/iptv-qa
sudo install -o root -g iptv-qa -m 0640 config/config.example.json /etc/iptv-qa/config.json
# Edit source URLs, media-tool paths and optional OBS settings before starting.
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/iptv-qa.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iptv-qa.timer
sudo systemctl start --no-block iptv-qa.service
```

The unit's optional SDK path is `/opt/iptv-QA/obs-sdk`. The checker allows all CPUs and has a 12 GiB example memory limit; adjust it for smaller hosts. `TimeoutStartSec=0` disables the overall oneshot timeout, including on systemd 219. Individual requests retain timeouts. Locks prevent overlapping runs; scheduled occurrences during a running service do not queue another full run.

### Schedule

The example schedule runs Monday, Wednesday and Sunday at **00:00 UTC**. Adjust it to your deployment schedule and timezone. The normal unit specifies UTC explicitly. systemd 219 cannot parse the timezone suffix: only for an `Asia/Shanghai` host, install `systemd/iptv-qa.timer.cst-legacy.example` as `/etc/systemd/system/iptv-qa.timer`. Other legacy host timezones need their own local-time expression. Always verify:

```bash
systemctl list-timers iptv-qa.timer --all
systemctl show iptv-qa.service -p MainPID -p ActiveState
cat /var/lib/iptv-qa/status.json
journalctl -u iptv-qa.service -n 40 --no-pager
```

## Dashboard and maintenance

`iptv-qa-web.service` binds to localhost:8088. Expose it deliberately through an authenticated reverse proxy if needed; it has no built-in authentication. Without `--public-base`, its allowed playlist routes can contain subscription URLs/tokens. It never serves config, environment, database or raw JSONL paths.

```bash
sudo systemctl enable --now iptv-qa-web.service
sudo systemctl start iptv-qa-upload.service   # retry validated publication upload
sudo systemctl start iptv-qa-summary.service  # regenerate sanitized history
sudo systemctl start iptv-qa-sets.service     # split current Top5 without rechecking
```

Detailed runs default to 14 days of retention, while the current publication and SQLite summaries persist. Back up private config and data independently. Read [PROGRESS](../PROGRESS.md) before upgrading a running deployment.

## Web TV player

The dashboard ships a password-gated web TV player at `/tv/`: after loading a playlist it offers a channel list with search, previous/next/random switching, remote-style number entry, fullscreen and a mobile layout. The source bar offers the published subscriptions shown on the dashboard (Set1/Set2/Set3/Top3) as one-click presets, and accepts any custom m3u URL.

When a channel fails, the player automatically walks the backup sets **Set1 -> Set2 -> Set3** (the three sets share channel names and order; a source is recognised by the file name at the end of its URL). A mode switch next to the random button selects the failure policy: **normal mode** shows an error after every set failed, while **enhanced mode** additionally moves to the next set after 10 seconds without data and, once all sets failed, keeps zapping to random channels and retrying the whole chain (up to 15 times) until something plays.

The server fetches playlists and proxies HLS manifests, segments, keys and audio tracks same-origin (honouring per-channel `User-Agent`/`Referer` options); browsers play through the vendored hls.js. **No transcoding by default**: only when the browser cannot decode the codec or a channel plays audio without video does the frontend request an on-demand server transcode (FFmpeg to H.264/AAC, capped at 720p/2 Mbps); at most 2 concurrent transcodes, idle ones stop after 90 seconds. Audio-only channels show an explicit "sound without picture" notice.

Set the access password (the player stays disabled without it):

```bash
sudo install -m 0600 config/tv.env.example /etc/iptv-qa/tv.env
# Edit the file: set IPTV_TV_PASSWORD to the real password; optionally point IPTV_TV_FFMPEG at an ffmpeg binary for transcoding.
sudo systemctl restart iptv-qa-web.service
```

Security boundaries: wrong passwords are delayed by one second and answered with 401; the session cookie is HttpOnly with SameSite=Strict and dies on service restart; the proxy only forwards addresses discovered inside loaded playlists and cannot be used as an open proxy; cloud metadata addresses are refused. Do not share the password; restrict source IPs with a firewall in production.
