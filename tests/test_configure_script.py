import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/configure.py'


def run_configure(output, data_dir, *sources):
    argv = [sys.executable, str(SCRIPT), '--output', str(output), '--data-dir', str(data_dir)]
    for source in sources:
        argv += ['--source', source]
    return subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))


class ConfigureSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def read_config(self):
        return json.loads((self.base / 'config.local.json').read_text(encoding='utf-8'))

    def test_local_playlist_path_becomes_file_uri(self):
        playlist = self.base / 'my.m3u'
        playlist.write_text('#EXTM3U\n', encoding='utf-8')
        result = run_configure(self.base / 'config.local.json', self.base / 'data', str(playlist))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_config()['sources'][0]['url'], playlist.resolve().as_uri())

    def test_http_url_passes_through(self):
        result = run_configure(self.base / 'config.local.json', self.base / 'data',
                               'https://example.test/list.m3u')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_config()['sources'][0]['url'], 'https://example.test/list.m3u')

    def test_invalid_source_is_rejected(self):
        result = run_configure(self.base / 'config.local.json', self.base / 'data',
                               str(self.base / 'missing.m3u'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--source', result.stderr)


if __name__ == '__main__':
    unittest.main()
