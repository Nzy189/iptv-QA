#!/usr/bin/env python3
"""Generate a local-only configuration without embedding any credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil


def normalize_source(value):
    """Pass URLs through; convert an existing local playlist file to a file:// URI."""
    if '://' in value:
        return value
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve().as_uri()
    raise ValueError('not a URL or an existing local playlist file: {}'.format(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='config.local.json')
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--source', action='append', help='Additional or replacement public/private subscription URL')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    target = Path(args.output).resolve()
    if target.exists() and not args.force:
        parser.error('Configuration exists; use another output or explicitly pass --force')
    data = Path(args.data_dir).resolve()
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root/'config/config.example.json').read_text(encoding='utf-8'))
    cfg.update(data_dir=str(data), language_metadata_path=str(data/'catalog-cache/language-metadata.json'),
               ffmpeg=shutil.which('ffmpeg') or '/usr/bin/ffmpeg',
               ffprobe=shutil.which('ffprobe') or '/usr/bin/ffprobe')
    if args.source:
        try:
            cfg['sources'] = [{'name': 'subscription_{}'.format(i+1), 'url': normalize_source(url)}
                              for i, url in enumerate(args.source)]
        except ValueError as exc:
            parser.error(str(exc))
    cfg['sources'] = [s for s in cfg['sources'] if not s.get('catalog')] + [
        {'name':'tvlist_zh', 'url':(data/'catalog/zh.m3u').as_uri(), 'optional':True, 'catalog':True}]
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        os.chmod(str(target), 0o600)
        json.dump(cfg, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print('Local configuration created; OBS disabled. Edit private URLs locally, never commit this file.')


if __name__ == '__main__':
    main()
