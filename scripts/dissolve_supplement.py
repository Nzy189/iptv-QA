"""One-time migration: dissolve the retired 'supplement' scope into 'english'.

The 直播频道 (live) view replaced 今日补充 (supplement). Every supplement
membership moves to the english scope; a channel that already belongs to
english keeps that membership and gains the supplement lines. Supplement
aliases are copied where the english alias is free. The script is idempotent:
a second run finds no supplement rows and changes nothing.

Usage:
    python3 scripts/dissolve_supplement.py /path/to/catalog.sqlite3

Back up the database before running. The 'live' scope starts empty and is
filled by the next QA run over live-tagged sources.
"""
import argparse
import datetime
import json
import sqlite3
import sys


def utcnow():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def dissolve(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    moved = merged = 0
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT channel_id FROM memberships WHERE scope='supplement'").fetchall()
            for row in rows:
                cid = row['channel_id']
                exists = db.execute("SELECT 1 FROM memberships WHERE channel_id=? AND scope='english'",
                                    (cid,)).fetchone()
                if exists:
                    db.execute("INSERT OR IGNORE INTO routes "
                               "SELECT channel_id,'english',stream_id,rank,priority,enabled,eligible,evidence,last_seen "
                               "FROM routes WHERE channel_id=? AND scope='supplement'", (cid,))
                    db.execute("DELETE FROM routes WHERE channel_id=? AND scope='supplement'", (cid,))
                    db.execute("DELETE FROM memberships WHERE channel_id=? AND scope='supplement'", (cid,))
                    merged += 1
                else:
                    db.execute("UPDATE memberships SET scope='english' WHERE channel_id=? AND scope='supplement'", (cid,))
                    db.execute("UPDATE routes SET scope='english' WHERE channel_id=? AND scope='supplement'", (cid,))
                    moved += 1
            db.execute("INSERT OR IGNORE INTO aliases SELECT 'english',alias,channel_id FROM aliases WHERE scope='supplement'")
            db.execute("DELETE FROM aliases WHERE scope='supplement'")
            revision = json.loads(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if moved or merged:
                revision += 1
                db.execute("INSERT OR REPLACE INTO meta VALUES ('revision',?)", (json.dumps(revision),))
                db.execute("INSERT INTO audit VALUES (NULL,'dissolve-supplement','','{}',?,?,?)",
                           (json.dumps({'moved_to_english': moved, 'merged_into_english': merged}), '{}', utcnow()))
    finally:
        db.close()
    return {'moved_to_english': moved, 'merged_into_english': merged, 'revision': revision}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='path to catalog.sqlite3')
    args = parser.parse_args(argv)
    print(json.dumps(dissolve(args.database)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
