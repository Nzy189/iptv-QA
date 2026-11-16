"""Compatibility entry point; implementation lives in iptv_qa.workflows.scheduled."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.workflows.scheduled")

if __name__ == "__main__":
    sys.exit(_impl.main())
else:
    sys.modules[__name__] = _impl
