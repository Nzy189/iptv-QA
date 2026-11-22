"""Structured TV request log and the session report built from it."""
import importlib.util
import io
import time
import json
import unittest
from pathlib import Path

from iptv_qa.playback import reqlog
from iptv_qa.playback.proxy import rewrite_playlist
from iptv_qa.playback.service import TVPlugin
from iptv_qa.playback.state import Registry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tv_session_report', ROOT / 'scripts' / 'tv_session_report.py')
report_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_mod)


def records(cm):
    return [json.loads(line.split('TVREQ ', 1)[1]) for line in cm.output if 'TVREQ ' in line]


class ReqlogTests(unittest.TestCase):
    def test_one_line_per_request_with_slow_flag(self):
        with self.assertLogs('iptv.tv', 'INFO') as cm:
            reqlog.begin('rstream', ip='1.2.3.4', m='GET')
            reqlog.note(dur=0.001, st=200)
            time.sleep(0.01)
            reqlog.end()
        rec = records(cm)[0]
        self.assertEqual((rec['kind'], rec['st']), ('rstream', 200))
        self.assertTrue(rec['slow'])
        self.assertIn('ms', rec)

    def test_note_outside_a_request_is_ignored(self):
        reqlog.note(st=1)          # background threads have no record
        self.assertIsNone(reqlog.end())

    def test_rewrite_attaches_channel_and_segment_duration(self):
        registry = Registry()
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:9.5,\na.ts\n#EXTINF:10,\nb.ts\n'
        out = rewrite_playlist(text, 'http://u/x/m.m3u8', {}, registry.register, meta={'ch': 'c1'})
        ids = [int(part) for part in out.replace('"', '/').split('/tv/stream/')[1:]
               for part in [part.split('\n')[0].split('/')[0]]]
        metas = [registry.meta(rid) for rid in ids]
        self.assertEqual(metas[0], {'ch': 'c1'})                 # key: no duration
        self.assertEqual(metas[1], {'ch': 'c1', 'dur': 9.5})
        self.assertEqual(metas[2], {'ch': 'c1', 'dur': 10.0})

    def test_rewrite_without_meta_keeps_two_argument_register(self):
        calls = []
        rewrite_playlist('#EXTM3U\n#EXTINF:6,\na.ts\n', 'http://u/', {},
                         lambda url, headers: calls.append(url) or 1)
        self.assertEqual(calls, ['http://u/a.ts'])


class _AbortingWriter:
    def write(self, data):
        raise BrokenPipeError(32, 'Broken pipe')


class _Handler:
    command = 'GET'

    def __init__(self):
        self.wfile = _AbortingWriter()
        self.close_connection = False

    def send_response(self, code):
        pass

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


class CancelledRequestTests(unittest.TestCase):
    def test_client_abort_is_recorded_not_raised(self):
        plugin = TVPlugin(b'pw')
        try:
            handler = _Handler()
            with self.assertLogs('iptv.tv', 'INFO') as cm:
                reqlog.begin('rstream', ip='x', m='GET')
                plugin.send_media(handler, 200, b'data', 'video/mp2t')
                reqlog.end()
            rec = records(cm)[0]
            self.assertTrue(rec['abort'])
            self.assertTrue(handler.close_connection)
        finally:
            plugin.close()


class SessionReportTests(unittest.TestCase):
    def lines(self):
        recs = [
            {'kind': 'race', 'm': 'GET', 'ip': 'a', 'ch': 'c1', 'name': 'One', 'ts': 100.0, 'ms': 900,
             'pick': 'race', 'st': 200},
            {'kind': 'rstream', 'm': 'GET', 'ip': 'a', 'ch': 'c1', 'dur': 10.0, 'ts': 101.0, 'ms': 2000,
             'st': 200, 'cache': 'miss', 'up_ms': 1900},
            {'kind': 'rstream', 'm': 'GET', 'ip': 'a', 'ch': 'c1', 'dur': 10.0, 'ts': 103.0, 'ms': 9000,
             'st': 200, 'slow': True, 'cache': 'miss', 'up_ms': 8900},
            {'kind': 'race', 'm': 'GET', 'ip': 'a', 'ch': 'c1', 'name': 'One', 'ts': 110.0, 'ms': 800,
             'pick': 'cache', 'st': 200},
            {'kind': 'race', 'm': 'GET', 'ip': 'a', 'ch': 'c2', 'name': 'Two', 'ts': 120.0, 'ms': 30000,
             'pick': 'single', 'st': 502, 'err': 'timeout'},
            {'kind': 'race', 'm': 'HEAD', 'ip': 'a', 'ch': 'c3', 'ts': 121.0, 'ms': 700, 'st': 200},
        ]
        return ['Nov 22 x python3[1]: 2026 INFO TVREQ ' + json.dumps(r) for r in recs] + \
            ['TVWARM ' + json.dumps({'host': 'h', 'ms': 1500, 'b': 10})]

    def test_report_groups_reloads_and_flags_problems(self):
        reqs, warms = report_mod.parse(self.lines())
        opens = report_mod.build_opens(reqs)
        self.assertEqual([o['ch'] for o in opens], ['c1', 'c2'])
        self.assertEqual(len(opens[0]['reloads']), 1)
        self.assertEqual(len(opens[0]['segments']), 2)
        out = io.StringIO()
        report_mod.report(reqs, warms, out=out)
        text = out.getvalue()
        self.assertIn('channel opens 2, segments served 2, slow segments 1 (50%)', text)
        self.assertIn('HEAD 1', text)
        self.assertIn('playlist 502 timeout', text)
        self.assertIn('no segment requested', text)
        self.assertIn('background warms n=1', text)


if __name__ == '__main__':
    unittest.main()
