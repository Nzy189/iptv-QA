"""Compatibility entry point; implementation lives in iptv_qa.reporting.history."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.reporting.history")
sys.modules[__name__] = _impl
