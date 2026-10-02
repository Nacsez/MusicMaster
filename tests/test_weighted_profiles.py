from __future__ import annotations

import math
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

try:
    import pytest

    pytestmark = [pytest.mark.unit, pytest.mark.regression]
except ImportError:
    pytestmark = ()

from music_mastering_tools.config import ReferenceSpec
from music_mastering_tools.dsp.profiles import (
    ReferenceGroup,
    ReferenceProfile,
    blend_reference_profiles,
    build_reference_inventory,
)
from tests.helpers import make_job, write_tone


def _profile(
    digest_digit: str,
    *,
    level_weight: float,
    frequency_weight: float,
    match_rms: float,
    final_coefficient: float,
    mid: tuple[float, ...],
    side: tuple[float, ...],
) -> ReferenceProfile:
    return ReferenceProfile(
        group=ReferenceGroup(
            content_sha256=digest_digit * 64,
            path=f"{digest_digit}.wav",
            source_indices=(0,),
            level_weight=level_weight,
            frequency_weight=frequency_weight,
        ),
        match_rms=match_rms,
        final_amplitude_coefficient=final_coefficient,
        mid_spectrum=np.asarray(mid, dtype=np.float64),
        side_spectrum=np.asarray(side, dtype=np.float64),
    )


class WeightedProfileTests(unittest.TestCase):
    def test_level_and_frequency_weights_are_independent_geometric_means(self) -> None:
        first = _profile(
            "a",
            level_weight=0.25,
            frequency_weight=0.75,
            match_rms=1.0,
            final_coefficient=1.0,
            mid=(1.0, 16.0),
            side=(16.0, 1.0),
        )
        second = _profile(
            "b",
            level_weight=0.75,
            frequency_weight=0.25,
            match_rms=16.0,
            final_coefficient=0.25,
            mid=(16.0, 1.0),
            side=(1.0, 16.0),
        )

        blended = blend_reference_profiles((second, first), floor=1e-6)

        self.assertAlmostEqual(blended.match_rms, 8.0)
        self.assertAlmostEqual(
            blended.final_amplitude_coefficient,
            math.exp(0.75 * math.log(0.25)),
        )
        np.testing.assert_allclose(
            blended.mid_spectrum,
            np.asarray((2.0, 8.0)),
            rtol=1e-15,
        )
        np.testing.assert_allclose(
            blended.side_spectrum,
            np.asarray((8.0, 2.0)),
            rtol=1e-15,
        )

    def test_profile_order_is_exactly_invariant_after_hash_sorting(self) -> None:
        first = _profile(
            "a",
            level_weight=0.5,
            frequency_weight=0.5,
            match_rms=0.1,
            final_coefficient=0.5,
            mid=(0.1, 2.0),
            side=(1e-6, 4.0),
        )
        second = _profile(
            "b",
            level_weight=0.5,
            frequency_weight=0.5,
            match_rms=0.8,
            final_coefficient=1.0,
            mid=(4.0, 0.2),
            side=(2.0, 0.5),
        )

        forward = blend_reference_profiles((first, second), floor=1e-6)
        reverse = blend_reference_profiles((second, first), floor=1e-6)

        self.assertEqual(forward.match_rms, reverse.match_rms)
        self.assertEqual(
            forward.final_amplitude_coefficient,
            reverse.final_amplitude_coefficient,
        )
        np.testing.assert_array_equal(forward.mid_spectrum, reverse.mid_spectrum)
        np.testing.assert_array_equal(forward.side_spectrum, reverse.side_spectrum)

    def test_one_effective_profile_uses_direct_values_without_log_floor(self) -> None:
        selected = _profile(
            "a",
            level_weight=1.0,
            frequency_weight=1.0,
            match_rms=0.25,
            final_coefficient=0.75,
            mid=(0.0, 1.0),
            side=(0.0, 0.5),
        )
        excluded = _profile(
            "b",
            level_weight=0.0,
            frequency_weight=0.0,
            match_rms=0.5,
            final_coefficient=0.5,
            mid=(4.0, 4.0),
            side=(4.0, 4.0),
        )

        blended = blend_reference_profiles((excluded, selected), floor=1e-3)

        self.assertTrue(blended.direct_level_profile)
        self.assertTrue(blended.direct_frequency_profile)
        self.assertEqual(blended.match_rms, selected.match_rms)
        np.testing.assert_array_equal(blended.mid_spectrum, selected.mid_spectrum)
        np.testing.assert_array_equal(blended.side_spectrum, selected.side_spectrum)

    def test_duplicate_content_is_coalesced_after_stable_weight_normalization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = write_tone(root / "reference-a.wav", frequency=440.0)
            duplicate = root / "reference-a-copy.wav"
            shutil.copyfile(first, duplicate)
            second = write_tone(root / "reference-b.wav", frequency=880.0)
            excluded = write_tone(root / "reference-excluded.wav", frequency=1760.0)
            job = make_job(root, reference=first)
            job = replace(
                job,
                references=(
                    ReferenceSpec(
                        str(first),
                        level_weight=1.0,
                        frequency_weight=2.0,
                    ),
                    ReferenceSpec(
                        str(duplicate),
                        level_weight=3.0,
                        frequency_weight=2.0,
                    ),
                    ReferenceSpec(
                        str(second),
                        level_weight=4.0,
                        frequency_weight=4.0,
                    ),
                    ReferenceSpec(
                        str(excluded),
                        level_weight=0.0,
                        frequency_weight=0.0,
                    ),
                ),
            )

            inventory = build_reference_inventory(job)

            self.assertEqual(len(inventory.requested), 4)
            self.assertEqual(len(inventory.groups), 3)
            duplicate_group = next(
                group for group in inventory.groups if len(group.source_indices) == 2
            )
            self.assertEqual(duplicate_group.source_indices, (0, 1))
            self.assertEqual(duplicate_group.level_weight, 0.5)
            self.assertEqual(duplicate_group.frequency_weight, 0.5)
            self.assertEqual(inventory.duplicate_group_count, 1)
            self.assertEqual(
                [group.content_sha256 for group in inventory.groups],
                sorted(group.content_sha256 for group in inventory.groups),
            )
            self.assertEqual(
                sum(group.effective for group in inventory.groups),
                2,
            )
            with self.assertRaises(KeyError):
                inventory.group_for_index(99)

    def test_profile_and_blend_guards_reject_invalid_analysis_products(self) -> None:
        level_only_group = ReferenceGroup(
            content_sha256="c" * 64,
            path="level-only.wav",
            source_indices=(0,),
            level_weight=1.0,
            frequency_weight=0.0,
        )
        frequency_group = ReferenceGroup(
            content_sha256="d" * 64,
            path="frequency.wav",
            source_indices=(1,),
            level_weight=0.0,
            frequency_weight=1.0,
        )
        with self.assertRaisesRegex(ValueError, "match_rms"):
            ReferenceProfile(
                level_only_group,
                match_rms=0.0,
                final_amplitude_coefficient=1.0,
                mid_spectrum=None,
                side_spectrum=None,
            )
        with self.assertRaisesRegex(ValueError, "final_amplitude"):
            ReferenceProfile(
                level_only_group,
                match_rms=1.0,
                final_amplitude_coefficient=float("inf"),
                mid_spectrum=None,
                side_spectrum=None,
            )
        with self.assertRaisesRegex(ValueError, "require Mid and Side"):
            ReferenceProfile(
                frequency_group,
                match_rms=1.0,
                final_amplitude_coefficient=1.0,
                mid_spectrum=None,
                side_spectrum=None,
            )
        for invalid_spectrum in (
            np.asarray([], dtype=np.float64),
            np.asarray([[1.0]], dtype=np.float64),
            np.asarray([-1.0], dtype=np.float64),
            np.asarray([float("nan")], dtype=np.float64),
        ):
            with self.subTest(spectrum=invalid_spectrum), self.assertRaises(ValueError):
                ReferenceProfile(
                    level_only_group,
                    match_rms=1.0,
                    final_amplitude_coefficient=1.0,
                    mid_spectrum=invalid_spectrum,
                    side_spectrum=None,
                )

        level_only = ReferenceProfile(
            level_only_group,
            match_rms=1.0,
            final_amplitude_coefficient=1.0,
            mid_spectrum=None,
            side_spectrum=None,
        )
        self.assertIsNone(level_only.to_details()["mid_spectrum_sha256"])
        with self.assertRaisesRegex(ValueError, "frequency profile"):
            blend_reference_profiles((level_only,), floor=1e-6)
        with self.assertRaisesRegex(ValueError, "blend floor"):
            blend_reference_profiles((level_only,), floor=0.0)

        frequency_only = ReferenceProfile(
            frequency_group,
            match_rms=1.0,
            final_amplitude_coefficient=1.0,
            mid_spectrum=np.asarray([1.0, 2.0]),
            side_spectrum=np.asarray([1.0, 2.0]),
        )
        with self.assertRaisesRegex(ValueError, "level profile"):
            blend_reference_profiles((frequency_only,), floor=1e-6)

        mismatched = _profile(
            "e",
            level_weight=1.0,
            frequency_weight=0.5,
            match_rms=1.0,
            final_coefficient=1.0,
            mid=(1.0, 2.0, 3.0),
            side=(1.0, 2.0, 3.0),
        )
        compatible = _profile(
            "f",
            level_weight=0.0,
            frequency_weight=0.5,
            match_rms=1.0,
            final_coefficient=1.0,
            mid=(1.0, 2.0),
            side=(1.0, 2.0),
        )
        with self.assertRaisesRegex(ValueError, "identical shapes"):
            blend_reference_profiles((mismatched, compatible), floor=1e-6)


if __name__ == "__main__":
    unittest.main()
