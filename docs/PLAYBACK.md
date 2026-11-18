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

- Three presets are shown: **综合频道**, **今日补充** and **英语频道**. With the library enabled, each reads its managed view with up to five ranked lines. Without it, the combined preset falls back to Set1–3 and matches backups by normalized channel identity, so missing/reordered rows do not play another channel. Existing playlist URLs and custom Top3 input still work.
- Set4 keeps its own order and no Set1–3 fallback. Supply `iptv_set4.m3u` under the public base URL, or put it in `DATA_DIR/extras/`; scheduled QA does not overwrite it. English uses `iptv_english.m3u` or `DATA_DIR/english/published/iptv_top3.m3u`.
- Normal mode reports failure after all channel lines fail. Enhanced mode also tries a backup after 10 seconds without data, then switches channel after every line fails. **顺序换台** (default) selects the next channel; toggle to **随机换台** for random selection without repeats. A scan stops after trying the whole list once; manual selection/retry starts a fresh scan. This mode never deletes channels or modifies OBS. Old channel error callbacks cannot switch a newly selected channel.
- **多源竞速** (hedged requests, on by default): when a channel has several lines, all of them (up to five) start loading at once and the first one to decode a frame plays; the others are dropped immediately. Lines that all fail with codec errors fall back to server transcoding, and other total failures continue sequentially at the first unraced line. The toggle in the controls bar turns racing off for sequential line-by-line attempts; racing briefly multiplies switch-time bandwidth by the number of racing lines. A genuine browser autoplay restriction still asks for the play button, but stale play requests from a race promotion no longer raise that notice, and the notice clears once video actually starts.
- The same-origin proxy rewrites HLS manifests, segments, keys, audio tracks and initialization URLs, preserving channel User-Agent/Referer settings. hls.js and its license are bundled. Browser codec support still matters.
- On a decode failure or sound without picture, the frontend can request FFmpeg fallback: H.264/AAC, up to 720p and 2 Mbps. Audio-only sources show a notice; transcoding cannot manufacture missing video.
- Default limits are two simultaneous transcodes and 90 seconds idle time. Tune `--transcode-limit`, `--transcode-idle` and `--ffmpeg`; `IPTV_TV_FFMPEG` is also supported. Direct proxy playback does not require FFmpeg. Service shutdown stops owned transcodes and removes their temporary files.

## Redirected live streams

Some channel URLs redirect to a CDN directory whose playlist uses relative file names. The proxy resolves child playlists, segments, keys and initialization files against the final response URL after redirects. Resolving them against the original channel URL can fetch the playlist successfully but request video from the wrong directory (HTTP 404), leaving browser playback unable to start even when FFmpeg can decode the source.

## Token racing subscription for TV apps

With the library enabled and one or more tokens configured (`IPTV_TV_TOKENS`, comma-separated, in the private service environment), any M3U player — an Apple TV or Android TV app, VLC, TiviMate — can load `http://HOST:PORT/tv/playlist.m3u?token=TOKEN&view=main|supplement|english` (view defaults to main). The playlist shows one entry per channel. Each entry's URL is a racing endpoint: the server concurrently probes the channel's ranked lines, relays the first line whose HLS manifest is valid and whose first segment returns data, and injects each line's User-Agent/Referer itself, so the player app never needs to understand `#EXTVLCOPT`. Rewritten child URLs use a parallel token-authenticated `/tv/rstream/` route, because the browser `/tv/stream/` route requires the session cookie.

Winners are cached per (view, channel) for about 60 seconds, so returning to a channel does not re-race; a race where every line fails falls back to ranked line 0. Tokens never unlock the management API or browser endpoints; they are checked in constant time, and leaving `IPTV_TV_TOKENS` empty disables the endpoints entirely (404). A token is as sensitive as the player password: keep it out of Git and logs, and prefer an HTTPS reverse proxy before sharing the subscription URL widely, since the token travels in the query string over the same plain HTTP as the existing password cookie.

## Access and scope

The player uses a shared password and an HttpOnly, SameSite=Strict session cookie. Incorrect passwords incur a one-second delay. Sessions and loaded channel registries live in memory and disappear after restart. This is a personal/small trusted-group player, not a multi-tenant account system. Dashboard routes are not protected by the player password.

The proxy restricts requests to URLs registered from loaded playlists and rejects selected cloud metadata addresses. An authenticated custom playlist can introduce upstream addresses: this is not comprehensive SSRF isolation. Restrict access to trusted users and network paths. Use HTTPS and appropriate access control when exposing the service beyond localhost.

Proxying and fallback transcoding consume server bandwidth/CPU per viewer. Browser autoplay and codec support vary by device. Public ECS cannot fetch private-network multicast without an appropriate upstream gateway. The old [feasibility note](WEB_PLAYER_FEASIBILITY.md) is historical; this page describes implemented behavior.

## Managed Top5 and recoverable cleanup

With the [channel library](LIBRARY.md) enabled, presets read database views and expose up to five lines per channel; `/tv/manage/` edits those same records. Failure cleanup (失效清理) quarantines within the current view, queues versioned publication and allows restoration. The homepage separates current library counts from historical QA outcomes and hides compatibility downloads under a secondary disclosure.

Managed sessions also show a **报错清理** (report) button in the controls bar. When the picture is not the channel's own content — an advertisement or a dead QR-code notice — click it and confirm the dialog: only the current line is disabled, not the whole channel, and the player immediately re-races the channel from its first remaining line (sequential retry when racing is off). The reported line has already left the live view that playback resolves against, so no session-level skip list is needed. When a channel has no lines left, the whole channel moves to the cleanup area and playback switches to the next channel. Both are reversible from the management page by re-enabling the line or restoring the channel, and scheduled QA never automatically restores a manually reported line. This is different from failure cleanup: failure cleanup is the automatic verdict on technical startup failures, while the report button is a viewer's report that the content itself is wrong.
