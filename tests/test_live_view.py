import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import iptv_checker as app
from iptv_qa.library.store import Library


@mock.patch.dict('sys.modules', {'certifi': types.SimpleNamespace(where=lambda: 'test-ca-bundle.pem')})
class LiveViewTests(unittest.TestCase):
    def entry(self, name, url, group='TV'):
        return app.parse_playlist('#EXTM3U\n#EXTINF:-1 group-title="{}",{}\n{}\n'.format(group, name, url))[0]

    def test_live_groups_publish_to_separate_live_view(self):
        live = self.entry('虎牙一起看', 'http://huya.test/live', '虎牙频道')
        normal = self.entry('News', 'http://one.test/live', '卫视频道')
        channels, endpoints = app.merge_entries([[live, normal]])
        probes = {i: {'height': 1080, 'seconds': 0} for i in endpoints}
        decodes = {i: {'ok': True, 'seconds': 4} for i in endpoints}
        texts, stats = app.render_playlists(channels, probes, decodes, {}, '',
                                            frozenset({'虎牙频道'}))
        five = [x for x in texts[5].splitlines() if not x.startswith('#')]
        live_urls = [x for x in texts['live'].splitlines() if not x.startswith('#')]
        self.assertEqual(five, ['http://one.test/live'])
        self.assertEqual(live_urls, ['http://huya.test/live'])
        self.assertEqual(stats['live_channels'], 1)
        # Channels outside the configured groups stay in the main view.
        texts, _ = app.render_playlists(channels, probes, decodes, {}, '')
        self.assertIn('http://huya.test/live', texts[5])
        self.assertNotIn('#EXTINF:', texts['live'])

    def test_dissolve_supplement_moves_rows_into_english(self):
        spec = importlib.util.spec_from_file_location(
            'dissolve_supplement', str(Path('scripts/dissolve_supplement.py')))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.sqlite3'
            library = Library(path)
            with library.connect() as db:
                db.execute("INSERT INTO channels VALUES ('c1','频道A','TV','active',1,'2026-01-01T00:00:00Z','{}')")
                db.execute("INSERT INTO memberships VALUES ('c1','supplement',0,1,1)")
                db.execute("INSERT INTO streams VALUES ('s1','http://one.test/live','{}')")
                db.execute("INSERT INTO routes VALUES ('c1','supplement','s1',0,NULL,1,1,'imported','2026-01-01T00:00:00Z')")
                db.execute("INSERT INTO aliases VALUES ('supplement','name:a','c1')")
                # c2 already belongs to english: its supplement lines merge in.
                db.execute("INSERT INTO channels VALUES ('c2','频道B','TV','active',1,'2026-01-01T00:00:00Z','{}')")
                db.execute("INSERT INTO memberships VALUES ('c2','english',0,1,1)")
                db.execute("INSERT INTO memberships VALUES ('c2','supplement',1,1,1)")
                db.execute("INSERT INTO streams VALUES ('s2','http://two.test/live','{}')")
                db.execute("INSERT INTO routes VALUES ('c2','supplement','s2',0,NULL,1,1,'imported','2026-01-01T00:00:00Z')")
            result = module.dissolve(path)
            self.assertEqual(result['moved_to_english'], 1)
            self.assertEqual(result['merged_into_english'], 1)
            self.assertEqual(result['revision'], 1)
            with library.connect() as db:
                for table in ('memberships', 'routes', 'aliases'):
                    self.assertIsNone(db.execute(
                        "SELECT 1 FROM {} WHERE scope='supplement'".format(table)).fetchone())
                moved = db.execute(
                    "SELECT channel_id FROM memberships WHERE scope='english' AND channel_id='c1'").fetchone()
                self.assertIsNotNone(moved)
                line = db.execute(
                    "SELECT channel_id FROM routes WHERE scope='english' AND stream_id='s1'").fetchone()
                self.assertEqual(line['channel_id'], 'c1')
                merged_line = db.execute(
                    "SELECT channel_id FROM routes WHERE scope='english' AND stream_id='s2'").fetchone()
                self.assertEqual(merged_line['channel_id'], 'c2')
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM memberships WHERE channel_id='c2'").fetchone()[0], 1)
            # A second run finds nothing and changes no revision.
            again = module.dissolve(path)
            self.assertEqual(again['moved_to_english'], 0)
            self.assertEqual(again['merged_into_english'], 0)
            self.assertEqual(again['revision'], 1)


if __name__ == '__main__':
    unittest.main()
