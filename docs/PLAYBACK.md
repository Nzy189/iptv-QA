# Browser playback

[English](PLAYBACK.md) | [简体中文](PLAYBACK.zh-CN.md) · [Architecture](ARCHITECTURE.md)

The implemented player imports local published subscriptions or any reachable M3U URL. It provides search, previous/next/random switching, numeric channel entry, fullscreen and mobile controls. It can run without catalogue discovery, detection, an OBS account or a local QA database.

## Start only the player

```bash
# Set a private password in the service environment; do not commit it.
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --host 127.0.0.1 --port 8089
```

Open `http://127.0.0.1:8089/tv/`, log in and paste an M3U URL. `/` redirects to `/tv/`; dashboard/status routes are not enabled in player-only mode. Use `--data-dir ./data` for your locally published preset files, or `--public-base https://YOUR_PUBLIC_PLAYLIST_BASE/` for remote presets. Custom URLs work without either. Without a password, player-only startup fails with a configuration message.

`python3 -m iptv_qa dashboard --data-dir ./data --port 8088` serves reports only, ignoring the player password. `python3 -m iptv_qa serve --data-dir ./data --port 8088` hosts both; in combined mode `/tv/` is enabled only when the password is set. Legacy `python3 web.py ...` retains combined behavior. See [service templates](DEPLOYMENT.md#independent-modules).

## Switching and media delivery

- Presets cover Set1/Set2/Set3/Top3. For recognized set filenames, failures fall back across the three aligned sets by channel index; keep their names and order aligned.
- Normal mode shows an error after backups fail. Enhanced mode also tries the next set after 10 seconds without data; if all sets fail it randomly tries another channel, up to 15 times. This is a playback recovery policy, not a new quality score.
- The same-origin proxy rewrites HLS manifests, segments, keys, audio tracks and initialization URLs, preserving channel User-Agent/Referer settings. hls.js and its license are bundled. Browser codec support still matters.
- On a decode failure or sound without picture, the frontend can request FFmpeg fallback: H.264/AAC, up to 720p and 2 Mbps. Audio-only sources show a notice; transcoding cannot manufacture missing video.
- Default limits are two simultaneous transcodes and 90 seconds idle time. Tune `--transcode-limit`, `--transcode-idle` and `--ffmpeg`; `IPTV_TV_FFMPEG` is also supported. Direct proxy playback does not require FFmpeg. Service shutdown stops owned transcodes and removes their temporary files.

## Access and scope

The player uses a shared password and an HttpOnly, SameSite=Strict session cookie. Incorrect passwords incur a one-second delay. Sessions and loaded channel registries live in memory and disappear after restart. This is a personal/small trusted-group player, not a multi-tenant account system. Dashboard routes are not protected by the player password.

The proxy restricts requests to URLs registered from loaded playlists and rejects selected cloud metadata addresses. An authenticated custom playlist can introduce upstream addresses: this is not comprehensive SSRF isolation. Restrict access to trusted users and network paths. Use HTTPS and appropriate access control when exposing the service beyond localhost.

Proxying and fallback transcoding consume server bandwidth/CPU per viewer. Browser autoplay and codec support vary by device. Public ECS cannot fetch private-network multicast without an appropriate upstream gateway. The old [feasibility note](WEB_PLAYER_FEASIBILITY.md) is historical; this page describes implemented behavior.
