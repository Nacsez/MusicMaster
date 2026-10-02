"""Engine selection kept separate from orchestration and validation."""

from __future__ import annotations

from ..config import EngineKind
from ..errors import CapabilityError
from .base import MasteringEngine
from .native import NativeMasteringEngine
from .upstream import UpstreamMatcheringEngine


def create_engine(kind: EngineKind) -> MasteringEngine:
    if kind is EngineKind.UPSTREAM:
        return UpstreamMatcheringEngine()
    if kind is EngineKind.NATIVE:
        return NativeMasteringEngine()
    raise CapabilityError(
        f"No engine adapter is registered for {kind!s}.",
        details={"engine": str(kind)},
    )


__all__ = ["create_engine"]
