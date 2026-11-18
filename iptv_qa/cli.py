"""Lazy module dispatcher: playback/reporting do not load the QA pipeline."""
import argparse
import importlib
import sys

COMMANDS = {
    'library': ('iptv_qa.library.cli', 'Manage channel library, publication and private backups'),
    'run': ('iptv_qa.workflows.scheduled', 'Full catalogue, checking and publication workflow'),
    'check': ('iptv_qa.detection.checker', 'Check configured sources and build ranked outputs'),
    'catalog': ('iptv_qa.catalog.cli', 'Download and classify candidates without checking media'),
    'publish': ('iptv_qa.publishing.cli', 'Upload an existing validated export without rechecking'),
    'report': ('iptv_qa.reporting.cli', 'Rebuild local history without fetching streams'),
    'serve': ('iptv_qa.server.http', 'Dashboard and optional player in one service'),
    'dashboard': ('iptv_qa.server.http', 'Reports and subscription downloads only'),
    'play': ('iptv_qa.server.http', 'Standalone password-gated browser playback'),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='iptv-qa', description='Independent IPTV modules: check, publish, observe and play.',
        epilog='\n'.join('{}: {}'.format(name, item[1]) for name,item in COMMANDS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=tuple(COMMANDS))
    if not argv or argv[0] in ('-h', '--help'):
        parser.print_help()
        return 0
    if argv[0].startswith('-'):
        # Preserve the original installed `iptv-qa --config ...` invocation.
        return importlib.import_module('iptv_qa.detection.checker').main(argv)
    command = parser.parse_args(argv[:1]).command
    module = importlib.import_module(COMMANDS[command][0])
    options = argv[1:]
    if command in ('dashboard', 'play'):
        return module.main(options, default_mode='dashboard' if command == 'dashboard' else 'player')
    return module.main(options)
