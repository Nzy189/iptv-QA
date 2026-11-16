"""Compatibility entry point; implementation lives in iptv_qa.server.http."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.server.http")

if __name__ == "__main__":
    sys.exit(_impl.main())
else:
    sys.modules[__name__] = _impl
