"""Compatibility entry point; implementation lives in iptv_qa.catalog.sources."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.catalog.sources")
sys.modules[__name__] = _impl
