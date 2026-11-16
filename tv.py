"""Compatibility entry point; implementation lives in iptv_qa.playback.service."""
import importlib
import sys

_impl = importlib.import_module("iptv_qa.playback.service")
sys.modules[__name__] = _impl
