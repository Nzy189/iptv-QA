"""Compatibility entry point; implementation lives in iptv_qa.detection.checker."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.detection.checker")

if __name__ == "__main__":
    sys.exit(_impl.main())
else:
    sys.modules[__name__] = _impl
