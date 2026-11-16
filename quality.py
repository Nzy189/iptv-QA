"""Compatibility entry point; implementation lives in iptv_qa.detection.quality."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.detection.quality")
sys.modules[__name__] = _impl
