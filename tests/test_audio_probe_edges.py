from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.audio_probe import (
    AudioProbeUnavailable,
    DefaultAudioProbe,
    SoundFileAudioProbe,
    WaveAudioProbe,
)


def _write_pcm_width(path: Path, sample_width: int) -> Path:
    if sample_width == 1:
        payload = bytes((0, 128, 255))
    elif sample_width == 3:
        payload = b"".join(
            value.to_bytes(3, byteorder="little", signed=True)
            for value in (-(1 << 23), 0, (1 << 23) - 1)
        )
    elif sample_width == 4:
        payload = struct.pack("<iii", -(1 << 31), 0, (1 << 31) - 1)
    else:
        raise AssertionError(f"unsupported test width: {sample_width}")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(sample_width)
        handle.setframerate(8_000)
        handle.writeframes(payload)
    return path


class AudioProbeEdgeTests(unittest.TestCase):
    def test_wave_probe_decodes_supported_integer_widths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for width in (1, 3, 4):
                with self.subTest(sample_width=width):
                    facts = WaveAudioProbe(block_frames=1).probe(
                        _write_pcm_width(root / f"pcm-{width}.wav", width)
                    )
                    self.assertEqual(facts.frames, 3)
                    self.assertEqual(facts.channels, 1)
                    self.assertEqual(facts.subtype, f"PCM_{width * 8}")
                    self.assertTrue(facts.is_finite)
                    self.assertGreaterEqual(facts.peak, 0.99)
                    self.assertGreaterEqual(facts.clipping_samples, 1)

    def test_probe_constructor_and_malformed_input_errors(self) -> None:
        with self.assertRaises(ValueError):
            WaveAudioProbe(block_frames=0)
        with self.assertRaises(ValueError):
            SoundFileAudioProbe(block_frames=0)
        with tempfile.TemporaryDirectory() as temporary:
            malformed = Path(temporary) / "malformed.wav"
            malformed.write_bytes(b"not audio")
            with self.assertRaises(AudioProbeUnavailable):
                WaveAudioProbe().probe(malformed)
            with self.assertRaises(AudioProbeUnavailable):
                SoundFileAudioProbe().probe(malformed)

    def test_default_probe_fallback_and_combined_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pcm = _write_pcm_width(root / "fallback.wav", 1)
            probe = DefaultAudioProbe()
            with mock.patch.object(
                probe.soundfile,
                "probe",
                side_effect=AudioProbeUnavailable("soundfile unavailable"),
            ):
                facts = probe.probe(pcm)
            self.assertEqual(facts.backend, "wave")

            bad_wav = root / "bad.wav"
            bad_wav.write_bytes(b"broken")
            with mock.patch.object(
                probe.soundfile,
                "probe",
                side_effect=AudioProbeUnavailable("soundfile failed"),
            ):
                with self.assertRaisesRegex(
                    AudioProbeUnavailable,
                    "WAV fallback failed",
                ):
                    probe.probe(bad_wav)

            unsupported = root / "bad.flac"
            unsupported.write_bytes(b"broken")
            with mock.patch.object(
                probe.soundfile,
                "probe",
                side_effect=AudioProbeUnavailable("soundfile failed"),
            ):
                with self.assertRaisesRegex(
                    AudioProbeUnavailable,
                    "bootstrap fallback only supports PCM WAV",
                ):
                    probe.probe(unsupported)

    def test_soundfile_probe_counts_nonfinite_samples(self) -> None:
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            self.skipTest(f"optional SoundFile stack unavailable: {exc}")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nonfinite.wav"
            samples = np.array(
                [[0.25, -0.25], [np.nan, np.inf], [-np.inf, 1.0]],
                dtype=np.float32,
            )
            sf.write(path, samples, 44_100, subtype="FLOAT")

            facts = SoundFileAudioProbe(block_frames=1).probe(path)

            self.assertEqual(facts.backend, "soundfile")
            self.assertEqual(facts.non_finite_samples, 3)
            self.assertEqual(facts.clipping_samples, 1)
            self.assertAlmostEqual(facts.peak, 1.0)


if __name__ == "__main__":
    unittest.main()
