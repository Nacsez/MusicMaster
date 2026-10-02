"""Generate small deterministic WAV fixtures without third-party dependencies.

The generator is deliberately conservative:

* every output path must remain inside the repository;
* existing files are never replaced;
* an existing byte mismatch is an error;
* a manifest records format metadata and SHA-256 digests; and
* ``--check`` performs a read-only regeneration and comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import sys
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PCM16_MAX: Final = 32_767
PCM16_MIN: Final = -32_768
TAU: Final = 2.0 * math.pi
PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY: Final = PROJECT_ROOT / "tests" / "fixtures" / "generated"
MANIFEST_NAME: Final = "manifest.json"
FIXTURE_SET_VERSION: Final = 1

FrameFactory = Callable[[int, int], tuple[int, ...]]


@dataclass(frozen=True)
class FixtureSpec:
    """A deterministic PCM fixture definition."""

    filename: str
    sample_rate: int
    channels: int
    frame_count: int
    purpose: str
    frame_factory: FrameFactory


def _pcm16(value: float) -> int:
    """Quantize a normalized floating-point sample to signed PCM16."""

    clamped = max(-1.0, min(1.0, value))
    quantized = int(round(clamped * PCM16_MAX))
    return max(PCM16_MIN, min(PCM16_MAX, quantized))


def _silence(_index: int, _sample_rate: int) -> tuple[int, ...]:
    return (0, 0)


def _near_silence(index: int, _sample_rate: int) -> tuple[int, ...]:
    left = 1 if index % 2 == 0 else -1
    right = -1 if index % 3 == 0 else 1
    return (left, right)


def _mono_tone(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    return (_pcm16(0.25 * math.sin(TAU * 440.0 * time_seconds)),)


def _resample_tone(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    left = 0.28 * math.sin(TAU * 330.0 * time_seconds)
    right = 0.24 * math.sin(TAU * 495.0 * time_seconds)
    return (_pcm16(left), _pcm16(right))


def _impulse(index: int, _sample_rate: int) -> tuple[int, ...]:
    if index == 0:
        return (PCM16_MAX, PCM16_MAX)
    if index == 127:
        return (-PCM16_MAX, PCM16_MAX)
    return (0, 0)


def _hard_limited(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    raw_left = 1.8 * math.sin(TAU * 220.0 * time_seconds)
    raw_right = 1.8 * math.sin(TAU * 330.0 * time_seconds)
    left = max(-0.8, min(0.8, raw_left))
    right = max(-0.8, min(0.8, raw_right))
    return (_pcm16(left), _pcm16(right))


def _clipped(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    raw_left = 1.4 * math.sin(TAU * 247.0 * time_seconds)
    raw_right = 1.4 * math.sin(TAU * 370.5 * time_seconds)
    left = PCM16_MIN if raw_left < -1.0 else _pcm16(raw_left)
    right = PCM16_MIN if raw_right < -1.0 else _pcm16(raw_right)
    return (left, right)


def _target_program(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    envelope = 0.68 + 0.20 * math.sin(TAU * 0.5 * time_seconds)
    transient = 0.12 if index % (sample_rate // 2) == 0 else 0.0
    left = envelope * (
        0.31 * math.sin(TAU * 110.0 * time_seconds)
        + 0.16 * math.sin(TAU * 440.0 * time_seconds)
        + 0.07 * math.sin(TAU * 1_760.0 * time_seconds)
    )
    right = envelope * (
        0.29 * math.sin(TAU * 110.0 * time_seconds)
        + 0.14 * math.sin(TAU * 660.0 * time_seconds)
        + 0.08 * math.sin(TAU * 2_200.0 * time_seconds)
    )
    return (_pcm16(left + transient), _pcm16(right - transient))


def _reference_program(index: int, sample_rate: int) -> tuple[int, ...]:
    time_seconds = index / sample_rate
    envelope = 0.72 + 0.16 * math.sin(TAU * 0.4 * time_seconds)
    transient = 0.10 if index % (sample_rate // 2) == 0 else 0.0
    left = envelope * (
        0.20 * math.sin(TAU * 110.0 * time_seconds)
        + 0.24 * math.sin(TAU * 440.0 * time_seconds)
        + 0.14 * math.sin(TAU * 1_760.0 * time_seconds)
    )
    right = envelope * (
        0.19 * math.sin(TAU * 110.0 * time_seconds)
        + 0.22 * math.sin(TAU * 660.0 * time_seconds)
        + 0.15 * math.sin(TAU * 2_200.0 * time_seconds)
    )
    return (_pcm16(left + transient), _pcm16(right - transient))


FIXTURES: Final[tuple[FixtureSpec, ...]] = (
    FixtureSpec(
        "target_program_stereo_44100.wav",
        44_100,
        2,
        6 * 44_100,
        "Nominal multi-tone target for integration and regression checks.",
        _target_program,
    ),
    FixtureSpec(
        "reference_program_stereo_44100.wav",
        44_100,
        2,
        6 * 44_100,
        "Spectrally distinct reference paired with the nominal target.",
        _reference_program,
    ),
    FixtureSpec(
        "silence_stereo_44100.wav",
        44_100,
        2,
        44_100,
        "Exact digital silence for zero-energy error handling.",
        _silence,
    ),
    FixtureSpec(
        "near_silence_stereo_44100.wav",
        44_100,
        2,
        44_100,
        "One-LSB signal for numerical-floor behavior.",
        _near_silence,
    ),
    FixtureSpec(
        "tone_mono_44100.wav",
        44_100,
        1,
        44_100,
        "Mono input for channel normalization behavior.",
        _mono_tone,
    ),
    FixtureSpec(
        "tone_stereo_48000.wav",
        48_000,
        2,
        48_000,
        "Non-internal sample rate for resampling behavior.",
        _resample_tone,
    ),
    FixtureSpec(
        "impulse_stereo_44100.wav",
        44_100,
        2,
        44_100,
        "Sparse impulses for alignment and filter-tail checks.",
        _impulse,
    ),
    FixtureSpec(
        "hard_limited_stereo_44100.wav",
        44_100,
        2,
        44_100,
        "Repeated equal peaks for limiter-detection warnings.",
        _hard_limited,
    ),
    FixtureSpec(
        "clipped_stereo_44100.wav",
        44_100,
        2,
        44_100,
        "Full-scale negative plateaus for clipping-detection warnings.",
        _clipped,
    ),
    FixtureSpec(
        "too_short_stereo_44100.wav",
        44_100,
        2,
        1_000,
        "Input shorter than Matchering's default FFT minimum.",
        _silence,
    ),
)


def _resolve_output_directory(raw_path: str | None) -> Path:
    requested = DEFAULT_OUTPUT_DIRECTORY if raw_path is None else Path(raw_path)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    resolved = requested.resolve(strict=False)

    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ValueError(
            f"Refusing to write or inspect fixtures outside the workspace: {resolved}"
        ) from error

    return resolved


def _render_wav(spec: FixtureSpec) -> bytes:
    buffer = io.BytesIO()
    frame_struct = struct.Struct("<" + ("h" * spec.channels))

    with wave.open(buffer, "wb") as output:
        output.setnchannels(spec.channels)
        output.setsampwidth(2)
        output.setframerate(spec.sample_rate)

        chunk = bytearray()
        for index in range(spec.frame_count):
            frame = spec.frame_factory(index, spec.sample_rate)
            if len(frame) != spec.channels:
                raise ValueError(
                    f"{spec.filename} produced {len(frame)} channels; expected {spec.channels}."
                )
            chunk.extend(frame_struct.pack(*frame))
            if len(chunk) >= 64 * 1_024:
                output.writeframesraw(chunk)
                chunk.clear()

        if chunk:
            output.writeframesraw(chunk)

    return buffer.getvalue()


def _manifest_bytes(audio_payloads: dict[str, bytes]) -> bytes:
    fixture_entries: list[dict[str, int | str]] = []
    specs_by_name = {spec.filename: spec for spec in FIXTURES}

    for filename in sorted(audio_payloads):
        spec = specs_by_name[filename]
        payload = audio_payloads[filename]
        fixture_entries.append(
            {
                "byte_count": len(payload),
                "channels": spec.channels,
                "filename": filename,
                "frame_count": spec.frame_count,
                "purpose": spec.purpose,
                "sample_rate": spec.sample_rate,
                "sample_width_bytes": 2,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    manifest = {
        "fixture_set_version": FIXTURE_SET_VERSION,
        "format": "RIFF/WAVE PCM signed 16-bit little-endian",
        "fixtures": fixture_entries,
        "generator": "scripts/generate_wav_fixtures.py",
    }
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _build_payloads() -> dict[str, bytes]:
    audio_payloads = {spec.filename: _render_wav(spec) for spec in FIXTURES}
    all_payloads = dict(audio_payloads)
    all_payloads[MANIFEST_NAME] = _manifest_bytes(audio_payloads)
    return all_payloads


def _verify_or_write(output_directory: Path, *, check_only: bool, verbose: bool) -> None:
    payloads = _build_payloads()
    missing: list[Path] = []
    mismatched: list[Path] = []

    for filename, expected in payloads.items():
        destination = output_directory / filename
        if not destination.exists():
            missing.append(destination)
            continue
        if not destination.is_file() or destination.read_bytes() != expected:
            mismatched.append(destination)

    if mismatched:
        formatted = "\n".join(f"  - {path}" for path in mismatched)
        raise RuntimeError(
            "Existing fixture content does not match the deterministic specification. "
            "No files were replaced:\n" + formatted
        )

    if check_only:
        if missing:
            formatted = "\n".join(f"  - {path}" for path in missing)
            raise RuntimeError("Fixture verification found missing files:\n" + formatted)
        print(f"Verified {len(payloads)} deterministic fixture files in {output_directory}.")
        return

    output_directory.mkdir(parents=True, exist_ok=True)
    for destination in missing:
        payload = payloads[destination.name]
        with destination.open("xb") as fixture_file:
            fixture_file.write(payload)
        if verbose:
            print(
                f"Wrote {destination.name}: {len(payload)} bytes, "
                f"sha256={hashlib.sha256(payload).hexdigest()}"
            )

    existing_count = len(payloads) - len(missing)
    print(
        f"Fixture set ready in {output_directory}: "
        f"{len(missing)} written, {existing_count} already verified."
    )


def _parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        help=("Workspace-relative output directory. Defaults to tests/fixtures/generated."),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and verify existing files without writing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every file written in addition to the summary.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the fixture generator CLI."""

    options = _parse_arguments(arguments)
    try:
        output_directory = _resolve_output_directory(options.output_dir)
        _verify_or_write(
            output_directory,
            check_only=bool(options.check),
            verbose=bool(options.verbose),
        )
    except (OSError, RuntimeError, ValueError, wave.Error) as error:
        print(f"fixture generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
