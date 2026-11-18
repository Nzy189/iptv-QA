# Channel library and management

[English](LIBRARY.md) | [简体中文](LIBRARY.zh-CN.md) · [Home](../README.md)

The optional channel library is the source of truth for managed playback and publication. Enable `channel_library.enabled`, set a shared `path` and `data_root`, and pass the private config to the web service using `--library-config` or `IPTV_LIBRARY_CONFIG`. Open `/tv/manage/` and log in with the player password. Custom remote playlists remain read-only inputs.

Channels use persistent UUIDs; aliases and exact request identities assist import matching. Metadata IDs are scoped and conflicting names are not blindly merged. A channel may belong to main, supplemental and English views. Each membership and line has independent operator controls; current QA eligibility is separate from manual enabled state. Reimport never resets manual removals or disabled lines. Ambiguous matching still needs operator review.

The manager supports search, pagination, name/group edits, line priority, line disable/restore, scoped channel quarantine/restore, impact preview, publication jobs, version rollback and encrypted-backup requests. Changes use revision checks; stale pages receive HTTP409. Detector results and browser failures are separate observations. Detailed raw URLs and credentials are not returned by the management list API.

The player shows one row per channel and tries up to five ranked lines. Main reads the library Top5 view; Set1–3 and Top3 remain compatibility exports. The homepage exposes playback and management, and collapses history/compatibility links; there is no prominent Top5 subscription button. Current library totals are distinct from the latest QA run, which may have been blocked from publication.

Destruction mode is opt-in and defaults off. A managed channel receives one 20-second startup budget across all lines. Exhausting the lines or budget quarantines the channel in the current view and queues publication. Actual playback, autoplay denial and manual/source switches cancel the startup timer. Login failure and offline state do not delete records. Quarantine is reversible from the management page; raw sources are not edited. A custom subscription cannot trigger managed deletion.

## Migration and publication

```bash
python3 -m iptv_qa library migrate --config /etc/iptv-qa/config.json
python3 -m iptv_qa library publish --config /etc/iptv-qa/config.json
python3 -m iptv_qa library worker --config /etc/iptv-qa/config.json
```

First migrate and test in an isolated directory, preserving current playlists. Migration imports existing main Top5/Top3, supplemental Set4 and English exports without claiming new media verification. An approved future QA result updates library eligibility; an in-flight pre-migration successful run can be reconciled by the worker. Failed/sample runs cannot replace the library.

Publication uses an immutable directory per revision and one serialized publisher. Local playlist pointers switch to generated exports; OBS versioned files upload before their fixed-key counterparts, and a sanitized manifest is uploaded last. Multi-object OBS updates are not atomic: partial delivery remains marked failed and retries the same immutable version. A queued publish is superseded if the database changes before processing; it cannot silently publish unreviewed later edits. Rollback restores operator control state from a known version and creates a new publication; it never erases the audit trail or revives a line lacking current eligibility.

## Private backup and recovery

Install the optional backup dependency (`cryptography`) in the worker environment. Configure `channel_library.backup` with `enabled`, a dedicated private `bucket`, `prefix` and `key_path`. Keep credentials in the service environment, never in source. The worker needs the OBS SDK; the web application only writes the library database and does not need bucket credentials.

SQLite backup API (or a transaction-consistent SQL snapshot on legacy Python) produces the database snapshot. A manifest and redacted config are packaged and authenticated-encrypted with Fernet; the encryption key is never packaged. Every backup is restored locally for integrity validation before upload. The object is uploaded with private ACL, checked for public-group grants, tested for anonymous denial, and downloaded again for byte verification. A failure never appears as a verified backup. The worker schedules daily backups and retains versioned history; retention/lifecycle deletion should be configured separately in the private bucket.

```bash
python3 -m iptv_qa library backup --config /etc/iptv-qa/config.json
python3 -m iptv_qa library restore --config /etc/iptv-qa/config.json \
  --snapshot PRIVATE_BACKUP.fernet --destination NEW_RECOVERY_DIRECTORY/catalog.sqlite3
```

Restore only writes a new destination and verifies authentication, checksum, schema and SQLite integrity. Activating a restored database requires stopping writers first; retain the previous database and configuration for rollback. Keep an independent copy of the encryption key: a backup alone is insufficient after total host loss. Backups and databases must never be hosted by the public HTTP service or committed to Git.
