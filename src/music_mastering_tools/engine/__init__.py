"""Mastering engine implementations and contracts."""

from .base import EngineCapabilities, EngineLogRecord, EngineRunResult, MasteringEngine
from .factory import create_engine
from .native import NATIVE_ENGINE_VERSION, NativeMasteringEngine
from .upstream import PINNED_MATCHERING_VERSION, UpstreamMatcheringEngine

__all__ = [
    "EngineCapabilities",
    "EngineLogRecord",
    "EngineRunResult",
    "MasteringEngine",
    "NATIVE_ENGINE_VERSION",
    "NativeMasteringEngine",
    "PINNED_MATCHERING_VERSION",
    "UpstreamMatcheringEngine",
    "create_engine",
]
