#!/usr/bin/env python3
"""Audit publishable source files and local Markdown links; print no secret values."""
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '__pycache__', '.venv', 'venv', 'data', 'build', 'dist', '.pytest_cache', '.claude'}
PRIVATE_NAMES = {'config.json', 'config.local.json', 'obs.env', '.env'}
PATTERNS = (
    re.compile(r'-{5}BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'(?i)[?&]token=[0-9a-f]{24,}'),
    re.compile(r'(?m)^[ \t]*(?:OBS_ACCESS_KEY_ID|OBS_SECRET_ACCESS_KEY)[ \t]*=[ \t]*[^\s#]+'),
)


def main():
    failures, count = [], 0
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED or part.endswith('.egg-info') for part in relative.parts):
            continue
        if path.name == '.local-context.md' or not path.is_file():
            continue
        count += 1
        if path.is_symlink() or path.name in PRIVATE_NAMES or path.suffix in ('.pem', '.key', '.sqlite3', '.jsonl', '.m3u', '.m3u8'):
            failures.append('{}: private/runtime artifact'.format(relative))
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            failures.append('{}: unexpected binary artifact'.format(relative))
            continue
        if any(pattern.search(text) for pattern in PATTERNS):
            failures.append('{}: possible secret pattern (value suppressed)'.format(relative))
        if path.suffix == '.md':
            for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
                target = link.split('#', 1)[0]
                if not target or re.match(r'^[a-z]+:', target, re.I):
                    continue
                target_path = (path.parent / unquote(target)).resolve()
                if ROOT != target_path and ROOT not in target_path.parents:
                    failures.append('{}: link escapes source root'.format(relative))
                elif not target_path.exists():
                    failures.append('{}: broken local link {}'.format(relative, target))
    if failures:
        print('\n'.join(failures))
        return 1
    print('PUBLICATION_AUDIT_OK files={} secrets=0 broken_local_links=0'.format(count))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
