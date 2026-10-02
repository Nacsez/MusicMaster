"""Streaming audio preflight probes.

The preferred probe uses SoundFile because it supports the same broad input
family as Matchering.  A standard-library WAV fallback keeps ``mmt validate``
useful before optional audio dependencies are installed.
"""

from __future__ import annotations

import math
import struct
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AudioFacts:
    """Measured facts used by preflight policy and run manifests."""

    path: str
    backend: str
    format: str
    subtype: str | None
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    rms: float
    clipping_samples: int
    non_finite_samples: int

    @property
    def is_finite(self) -> bool:
        return self.non_finite_samples == 0


class AudioProbe(Protocol):
    """Port implemented by audio metadata/sample scanners."""

    def probe(self, path: str | Path) -> AudioFacts:
        """Inspect an input without changing it."""


class AudioProbeUnavailable(RuntimeError):
    """Raised when no installed probe can read a requested input."""


class SoundFileAudioProbe:
    """Block-oriented probe for SoundFile/libsndfile inputs."""

    def __init__(self, *, block_frames: int = 65_536, clipping_peak: float = 1.0):
        if block_frames <= 0:
            raise ValueError("block_frames must be positive")
        self.block_frames = block_frames
        self.clipping_peak = clipping_peak

    def probe(self, path: str | Path) -> AudioFacts:
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            raise AudioProbeUnavailable(
                "SoundFile probe requires the 'numpy' and 'soundfile' packages"
            ) from exc

        source = Path(path)
        peak = 0.0
        sum_squares = 0.0
        sample_count = 0
        clipping_samples = 0
        non_finite_samples = 0

        try:
            with sf.SoundFile(source) as handle:
                sample_rate = int(handle.samplerate)
                channels = int(handle.channels)
                frames = int(len(handle))
                format_name = str(handle.format)
                subtype = str(handle.subtype)

                for block in handle.blocks(
                    blocksize=self.block_frames,
                    dtype="float64",
                    always_2d=True,
                ):
                    finite_mask = np.isfinite(block)
                    non_finite_samples += int(block.size - np.count_nonzero(finite_mask))
                    if not finite_mask.all():
                        block = np.where(finite_mask, block, 0.0)
                    absolute = np.abs(block)
                    if absolute.size:
                        peak = max(peak, float(absolute.max()))
                        clipping_samples += int(np.count_nonzero(absolute >= self.clipping_peak))
                        sum_squares += float(np.square(block).sum(dtype=np.float64))
                        sample_count += int(block.size)
        except (OSError, RuntimeError) as exc:
            raise AudioProbeUnavailable(f"cannot inspect audio file '{source}': {exc}") from exc

        return _facts(
            source,
            backend="soundfile",
            format_name=format_name,
            subtype=subtype,
            sample_rate=sample_rate,
            channels=channels,
            frames=frames,
            peak=peak,
            sum_squares=sum_squares,
            sample_count=sample_count,
            clipping_samples=clipping_samples,
            non_finite_samples=non_finite_samples,
        )


class WaveAudioProbe:
    """Standard-library PCM WAV probe used during minimal bootstrap."""

    def __init__(self, *, block_frames: int = 16_384, clipping_peak: float = 1.0):
        if block_frames <= 0:
            raise ValueError("block_frames must be positive")
        self.block_frames = block_frames
        self.clipping_peak = clipping_peak

    def probe(self, path: str | Path) -> AudioFacts:
        source = Path(path)
        try:
            with wave.open(str(source), "rb") as handle:
                channels = handle.getnchannels()
                sample_width = handle.getsampwidth()
                sample_rate = handle.getframerate()
                frames = handle.getnframes()
                if sample_width not in (1, 2, 3, 4):
                    raise AudioProbeUnavailable(
                        f"unsupported PCM WAV sample width: {sample_width} bytes"
                    )

                peak = 0.0
                sum_squares = 0.0
                sample_count = 0
                clipping_samples = 0
                while True:
                    raw = handle.readframes(self.block_frames)
                    if not raw:
                        break
                    for value in _decode_pcm(raw, sample_width):
                        absolute = abs(value)
                        peak = max(peak, absolute)
                        sum_squares += value * value
                        sample_count += 1
                        if absolute >= self.clipping_peak:
                            clipping_samples += 1
        except (OSError, EOFError, wave.Error) as exc:
            raise AudioProbeUnavailable(f"cannot inspect PCM WAV '{source}': {exc}") from exc

        return _facts(
            source,
            backend="wave",
            format_name="WAV",
            subtype=f"PCM_{sample_width * 8}",
            sample_rate=sample_rate,
            channels=channels,
            frames=frames,
            peak=peak,
            sum_squares=sum_squares,
            sample_count=sample_count,
            clipping_samples=clipping_samples,
            non_finite_samples=0,
        )


class DefaultAudioProbe:
    """Use SoundFile when available and fall back to PCM WAV inspection."""

    def __init__(self, *, clipping_peak: float = 1.0):
        self.soundfile = SoundFileAudioProbe(clipping_peak=clipping_peak)
        self.wave = WaveAudioProbe(clipping_peak=clipping_peak)

    def probe(self, path: str | Path) -> AudioFacts:
        source = Path(path)
        try:
            return self.soundfile.probe(source)
        except AudioProbeUnavailable as soundfile_error:
            if source.suffix.casefold() not in {".wav", ".wave"}:
                raise AudioProbeUnavailable(
                    f"{soundfile_error}; the bootstrap fallback only supports PCM WAV"
                ) from soundfile_error
            try:
                return self.wave.probe(source)
            except AudioProbeUnavailable as wave_error:
                raise AudioProbeUnavailable(
                    f"SoundFile probe failed ({soundfile_error}); "
                    f"WAV fallback failed ({wave_error})"
                ) from wave_error


def _facts(
    path: Path,
    *,
    backend: str,
    format_name: str,
    subtype: str | None,
    sample_rate: int,
    channels: int,
    frames: int,
    peak: float,
    sum_squares: float,
    sample_count: int,
    clipping_samples: int,
    non_finite_samples: int,
) -> AudioFacts:
    rms = math.sqrt(sum_squares / sample_count) if sample_count else 0.0
    duration = frames / sample_rate if sample_rate > 0 else 0.0
    return AudioFacts(
        path=str(path.resolve()),
        backend=backend,
        format=format_name,
        subtype=subtype,
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        duration_seconds=duration,
        peak=peak,
        rms=rms,
        clipping_samples=clipping_samples,
        non_finite_samples=non_finite_samples,
    )


def _decode_pcm(raw: bytes, sample_width: int) -> Iterable[float]:
    if sample_width == 1:
        for value in raw:
            yield (value - 128) / 128.0
        return

    if sample_width in (2, 4):
        code = "h" if sample_width == 2 else "i"
        maximum = float(1 << (sample_width * 8 - 1))
        count = len(raw) // sample_width
        for (value,) in struct.iter_unpack(f"<{code}", raw[: count * sample_width]):
            yield value / maximum
        return

    maximum = float(1 << 23)
    for offset in range(0, len(raw) - 2, 3):
        chunk = raw[offset : offset + 3]
        value = int.from_bytes(chunk, byteorder="little", signed=False)
        if value & 0x800000:
            value -= 1 << 24
        yield value / maximum
