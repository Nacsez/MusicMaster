"""Small deterministic test helpers with no third-party dependencies."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from music_mastering_tools.config import (
    ExecutionConfig,
    JobConfig,
    OutputSpec,
    ReferenceSpec,
)


def write_tone(
    path: Path,
    *,
    frequency: float = 440.0,
    sample_rate: int = 44_100,
    frames: int = 8_192,
    channels: int = 2,
    amplitude: float = 0.25,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_struct = struct.Struct("<" + ("h" * channels))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        payload = bytearray()
        for index in range(frames):
            values = tuple(
                int(
                    round(
                        amplitude
                        * 32_767
                        * math.sin(
                            2.0 * math.pi * frequency * (1.0 + 0.25 * channel) * index / sample_rate
                            + 0.2 * channel
                        )
                    )
                )
                for channel in range(channels)
            )
            payload.extend(frame_struct.pack(*values))
        handle.writeframes(payload)
    return path


def write_silence(
    path: Path,
    *,
    sample_rate: int = 44_100,
    frames: int = 8_192,
    channels: int = 2,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * channels * frames)
    return path


def make_job(
    root: Path,
    *,
    target: Path | None = None,
    reference: Path | None = None,
    output: Path | None = None,
    job_id: str = "test-job",
) -> JobConfig:
    target_path = target or write_tone(root / "target.wav", frequency=330.0)
    reference_path = reference or write_tone(root / "reference.wav", frequency=660.0)
    output_path = output or root / "output.wav"
    return JobConfig(
        target=str(target_path),
        references=(ReferenceSpec(str(reference_path)),),
        outputs=(OutputSpec(str(output_path)),),
        execution=ExecutionConfig(job_id=job_id),
    )
