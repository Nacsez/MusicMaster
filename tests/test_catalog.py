"""Contract tests for the private content-addressed catalog."""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from music_mastering_tools.catalog import (
    AudioFacts,
    CatalogConflictError,
    CatalogError,
    CatalogFormatError,
    CatalogRole,
    CatalogSelection,
    CatalogStore,
    LocationState,
    ReferenceSetMember,
    SelectedReference,
    SelectedTrack,
    default_catalog_path,
)
from music_mastering_tools.manifest import ArtifactManifest, RunManifest


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class CatalogStoreTests(unittest.TestCase):
    def test_context_manager_creates_versioned_wal_database_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "private" / "catalog.sqlite3"
            store = CatalogStore(database)
            catalog_id = store.catalog_id
            self.assertTrue(catalog_id.startswith("cat_"))
            self.assertEqual(store.revision, 0)

            connection = sqlite3.connect(database)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0],
                    "wal",
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    0,
                )
            finally:
                connection.close()

            store.close()
            store.close()
            with self.assertRaisesRegex(CatalogError, "closed"):
                _ = store.catalog_id
            self.assertEqual(
                default_catalog_path(root),
                root.resolve() / ".mmt" / "catalog" / "catalog.sqlite3",
            )

    def test_add_deduplicates_content_and_retains_multiple_locations_and_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _write(root / "one.wav", b"same-audio-bytes")
            second = _write(root / "copies" / "two.wav", first.read_bytes())
            facts = AudioFacts(
                sample_rate=44_100,
                channels=2,
                frames=100,
                duration_seconds=100 / 44_100,
                format="WAV",
                subtype="PCM_24",
            )
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                target = catalog.add_track(
                    first,
                    roles=(CatalogRole.TARGET,),
                    label="source",
                    audio_facts=facts,
                )
                duplicate = catalog.add_track(
                    second,
                    roles=(CatalogRole.REFERENCE,),
                    label="source",
                    audio_facts=facts,
                )

                self.assertEqual(target.track_id, duplicate.track_id)
                self.assertTrue(target.track_id.startswith("trk_sha256_"))
                updated = catalog.get_track(target.track_id)
                self.assertEqual(
                    updated.roles,
                    (CatalogRole.REFERENCE, CatalogRole.TARGET),
                )
                self.assertEqual(updated.audio_facts, facts)
                locations = catalog.list_locations(target.track_id)
                self.assertEqual(len(locations), 2)
                self.assertNotEqual(locations[0].location_id, locations[1].location_id)
                self.assertTrue(
                    all(location.state is LocationState.AVAILABLE for location in locations)
                )
                self.assertIn(
                    catalog.preferred_location(target.track_id),
                    locations,
                )

    def test_changed_path_is_visible_and_relink_requires_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_bytes = b"original-track"
            original = _write(root / "track.wav", original_bytes)
            moved = _write(root / "moved.wav", original_bytes)
            wrong = _write(root / "wrong.wav", b"different-track")

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                track = catalog.add_track(original, roles=(CatalogRole.TARGET,))
                old_location = catalog.list_locations(track.track_id)[0]
                original.write_bytes(b"replacement-content")

                verified = catalog.verify(track.track_id)
                self.assertEqual(verified[0].state, LocationState.CHANGED)
                with self.assertRaises(CatalogConflictError):
                    catalog.add_track(original, roles=(CatalogRole.REFERENCE,))
                with self.assertRaises(CatalogConflictError):
                    catalog.relink(track.track_id, wrong)

                new_location = catalog.relink(
                    track.track_id,
                    moved,
                    archive_location_id=old_location.location_id,
                )
                self.assertEqual(new_location.state, LocationState.AVAILABLE)
                self.assertEqual(
                    {
                        item.location_id: item.state
                        for item in catalog.list_locations(track.track_id)
                    }[old_location.location_id],
                    LocationState.ARCHIVED,
                )

                self.assertTrue(catalog.archive_track(track.track_id).archived)
                self.assertFalse(catalog.archive_track(track.track_id, archived=False).archived)
                states = {item.state for item in catalog.list_locations(track.track_id)}
                self.assertIn(LocationState.AVAILABLE, states)
                self.assertIn(LocationState.CHANGED, states)

    def test_outer_transaction_rolls_back_nested_catalog_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write(root / "source.wav", b"source")
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                original_revision = catalog.revision
                with self.assertRaisesRegex(RuntimeError, "abort"):
                    with catalog.transaction():
                        catalog.add_track(source, roles=(CatalogRole.TARGET,))
                        raise RuntimeError("abort")
                self.assertEqual(catalog.list_tracks(), ())
                self.assertEqual(catalog.revision, original_revision)

    def test_reference_sets_create_weighted_multi_reference_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                target = catalog.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                reference_a = catalog.add_track(
                    _write(root / "reference-a.wav", b"reference-a"),
                    roles=(CatalogRole.REFERENCE,),
                )
                reference_b = catalog.add_track(
                    _write(root / "reference-b.wav", b"reference-b"),
                    roles=(CatalogRole.REFERENCE,),
                )
                reference_set = catalog.create_reference_set("Balanced")
                reference_set = catalog.set_reference_member(
                    reference_set.set_id,
                    reference_a.track_id,
                    level_weight=3,
                    frequency_weight=1,
                )
                reference_set = catalog.set_reference_member(
                    reference_set.set_id,
                    reference_b.track_id,
                    level_weight=1,
                    frequency_weight=3,
                )
                self.assertEqual(
                    tuple(member.ordinal for member in reference_set.members),
                    (0, 1),
                )

                selection = catalog.build_selection(
                    target.track_id,
                    reference_set_id=reference_set.set_id,
                )
                self.assertEqual(selection.normalized_level_weights, (0.75, 0.25))
                self.assertEqual(selection.normalized_frequency_weights, (0.25, 0.75))
                self.assertTrue(selection.selection_id.startswith("sel_sha256_"))

                reference_set = catalog.remove_reference_member(
                    reference_set.set_id,
                    reference_a.track_id,
                )
                self.assertEqual(reference_set.members[0].ordinal, 0)
                single = catalog.build_selection(
                    target.track_id,
                    reference_set_id=reference_set.set_id,
                )
                self.assertEqual(len(single.references), 1)

    def test_reference_set_rename_replace_and_delete_are_strict_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                reference_a = catalog.add_track(
                    _write(root / "reference-a.wav", b"reference-a"),
                    roles=(CatalogRole.REFERENCE,),
                )
                reference_b = catalog.add_track(
                    _write(root / "reference-b.wav", b"reference-b"),
                    roles=(CatalogRole.REFERENCE,),
                )
                target = catalog.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                reference_set = catalog.create_reference_set("Original")
                catalog.set_reference_member(reference_set.set_id, reference_a.track_id)
                catalog.create_reference_set("Reserved")

                renamed = catalog.rename_reference_set(
                    reference_set.set_id,
                    "  Renamed Blend  ",
                )
                self.assertEqual(renamed.set_id, reference_set.set_id)
                self.assertEqual(renamed.name, "Renamed Blend")
                self.assertEqual(
                    tuple(member.track_id for member in renamed.members),
                    (reference_a.track_id,),
                )

                revision_before_conflict = catalog.revision
                with self.assertRaisesRegex(CatalogConflictError, "already exists"):
                    catalog.rename_reference_set(reference_set.set_id, "reserved")
                self.assertEqual(catalog.revision, revision_before_conflict)
                self.assertEqual(
                    catalog.get_reference_set(reference_set.set_id).name,
                    "Renamed Blend",
                )
                with self.assertRaises(ValueError):
                    catalog.rename_reference_set(reference_set.set_id, "  ")

                replaced = catalog.replace_reference_set_members(
                    reference_set.set_id,
                    (
                        ReferenceSetMember(
                            track_id=reference_a.track_id,
                            ordinal=1,
                            level_weight=1,
                            frequency_weight=3,
                        ),
                        ReferenceSetMember(
                            track_id=reference_b.track_id,
                            ordinal=0,
                            level_weight=3,
                            frequency_weight=1,
                        ),
                    ),
                )
                self.assertEqual(
                    tuple(member.track_id for member in replaced.members),
                    (reference_b.track_id, reference_a.track_id),
                )
                self.assertEqual(
                    tuple(
                        (member.ordinal, member.level_weight, member.frequency_weight)
                        for member in replaced.members
                    ),
                    ((0, 3, 1), (1, 1, 3)),
                )
                replaced = catalog.replace_reference_set_members(
                    reference_set.set_id,
                    (
                        (reference_a.track_id, 2.0, 4.0),
                        (reference_b.track_id, 4.0, 2.0),
                    ),
                )
                self.assertEqual(
                    tuple(
                        (
                            member.track_id,
                            member.ordinal,
                            member.level_weight,
                            member.frequency_weight,
                        )
                        for member in replaced.members
                    ),
                    (
                        (reference_a.track_id, 0, 2.0, 4.0),
                        (reference_b.track_id, 1, 4.0, 2.0),
                    ),
                )

                expected_members = replaced.members
                revision_before_invalid = catalog.revision
                invalid_replacements = (
                    (
                        ReferenceSetMember(reference_a.track_id, 0, 1, 1),
                        ReferenceSetMember(reference_a.track_id, 1, 1, 1),
                    ),
                    (
                        ReferenceSetMember(reference_a.track_id, 0, 1, 1),
                        ReferenceSetMember(reference_b.track_id, 2, 1, 1),
                    ),
                    (ReferenceSetMember(target.track_id, 0, 1, 1),),
                )
                for members in invalid_replacements:
                    with self.subTest(members=members):
                        with self.assertRaises((CatalogConflictError, ValueError)):
                            catalog.replace_reference_set_members(
                                reference_set.set_id,
                                members,
                            )
                        self.assertEqual(
                            catalog.get_reference_set(reference_set.set_id).members,
                            expected_members,
                        )
                        self.assertEqual(catalog.revision, revision_before_invalid)
                with self.assertRaises(TypeError):
                    catalog.replace_reference_set_members(
                        reference_set.set_id,
                        "not-members",  # type: ignore[arg-type]
                    )
                with self.assertRaises(TypeError):
                    catalog.replace_reference_set_members(
                        reference_set.set_id,
                        (object(),),  # type: ignore[arg-type]
                    )

                cleared = catalog.replace_reference_set_members(
                    reference_set.set_id,
                    (),
                )
                self.assertEqual(cleared.members, ())
                deleted = catalog.delete_reference_set(reference_set.set_id)
                self.assertEqual(deleted.set_id, reference_set.set_id)
                self.assertEqual(deleted.name, "Renamed Blend")
                with self.assertRaisesRegex(CatalogError, "unknown reference set"):
                    catalog.get_reference_set(reference_set.set_id)
                self.assertEqual(catalog.get_track(reference_a.track_id), reference_a)
                self.assertEqual(catalog.get_track(reference_b.track_id), reference_b)

    def test_selection_json_is_strict_deterministic_and_atomically_importable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "source.sqlite3") as source:
                target = source.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                reference = source.add_track(
                    _write(root / "reference.wav", b"reference"),
                    roles=(CatalogRole.REFERENCE,),
                )
                selection = source.build_selection(
                    target.track_id,
                    reference_ids=(reference.track_id,),
                )
                document = selection.to_json()
                self.assertEqual(CatalogSelection.from_json(document), selection)
                self.assertEqual(
                    CatalogSelection.from_json(document).to_json(),
                    document,
                )

            with CatalogStore(root / "destination.sqlite3") as destination:
                imported = destination.import_selection(document)
                self.assertEqual(imported.selection_id, selection.selection_id)
                self.assertEqual(
                    destination.get_track(target.track_id).roles,
                    (CatalogRole.TARGET,),
                )
                self.assertEqual(
                    destination.get_track(reference.track_id).roles,
                    (CatalogRole.REFERENCE,),
                )

            duplicate_key = document.replace(
                '"schema_version": 1,',
                '"schema_version": 1, "schema_version": 1,',
                1,
            )
            with self.assertRaisesRegex(CatalogFormatError, "duplicate JSON key"):
                CatalogSelection.from_json(duplicate_key)

            parsed = json.loads(document)
            parsed["unexpected"] = True
            with self.assertRaisesRegex(CatalogFormatError, "unknown field"):
                CatalogSelection.from_json(json.dumps(parsed))

            non_finite = document.replace('"level_weight": 1.0', '"level_weight": 1e999')
            with self.assertRaisesRegex(CatalogFormatError, "non-finite"):
                CatalogSelection.from_json(non_finite)

    def test_selection_import_rejects_tampering_without_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "source.sqlite3") as source:
                target = source.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                reference = source.add_track(
                    _write(root / "reference.wav", b"reference"),
                    roles=(CatalogRole.REFERENCE,),
                )
                selection = source.build_selection(
                    target.track_id,
                    reference_ids=(reference.track_id,),
                )

            parsed = json.loads(selection.to_json())
            parsed["references"][0]["path"] = str(_write(root / "imposter.wav", b"not-reference"))
            tampered = json.dumps(parsed)
            with CatalogStore(root / "destination.sqlite3") as destination:
                with self.assertRaises(CatalogConflictError):
                    destination.import_selection(tampered)
                self.assertEqual(destination.list_tracks(), ())

    def test_runs_and_all_four_artifact_roles_are_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "catalog.sqlite3") as catalog:
                source_path = _write(root / "target.wav", b"target")
                target = catalog.add_track(source_path, roles=(CatalogRole.TARGET,))
                catalog.record_run("run-1", "running")
                paths = {
                    CatalogRole.TARGET: source_path,
                    CatalogRole.REFERENCE: _write(root / "reference.wav", b"reference"),
                    CatalogRole.GENERATED_OUTPUT: _write(root / "master.wav", b"master"),
                    CatalogRole.AUDIT: _write(root / "events.jsonl", b"{}\n"),
                }
                for role, path in paths.items():
                    catalog.index_artifact(
                        "run-1",
                        path,
                        role=role,
                        track_id=target.track_id if role is CatalogRole.TARGET else None,
                        metadata={"role": role.value},
                    )
                catalog.record_run("run-1", "completed")

                run = catalog.get_run("run-1")
                self.assertEqual(run.status, "completed")
                artifacts = catalog.list_run_artifacts("run-1")
                self.assertEqual({item.role for item in artifacts}, set(CatalogRole))
                self.assertTrue(
                    all(item.artifact_id.startswith("art_sha256_") for item in artifacts)
                )
                with self.assertRaises(TypeError):
                    artifacts[0].metadata["mutate"] = True  # type: ignore[index]

    def test_complete_catalog_export_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "source.sqlite3") as source:
                target = source.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                    label="Target",
                )
                reference = source.add_track(
                    _write(root / "reference.wav", b"reference"),
                    roles=(CatalogRole.REFERENCE,),
                )
                reference_set = source.create_reference_set("Primary")
                source.set_reference_member(
                    reference_set.set_id,
                    reference.track_id,
                    level_weight=0.25,
                    frequency_weight=0.75,
                )
                selection = source.build_selection(
                    target.track_id,
                    reference_set_id=reference_set.set_id,
                )
                source.record_run("run-export", "completed", selection=selection)
                source.index_artifact(
                    "run-export",
                    _write(root / "master.wav", b"master"),
                    role=CatalogRole.GENERATED_OUTPUT,
                    media_type="audio/wav",
                    metadata={"subtype": "PCM_24"},
                )
                document = source.export_json()

            with CatalogStore(root / "destination.sqlite3") as destination:
                destination.import_json(document)
                self.assertEqual(len(destination.list_tracks()), 2)
                self.assertEqual(len(destination.list_reference_sets()), 1)
                self.assertEqual(len(destination.list_runs()), 1)
                self.assertEqual(
                    len(destination.list_run_artifacts("run-export")),
                    1,
                )
                rebuilt = destination.build_selection(
                    target.track_id,
                    reference_set_id=reference_set.set_id,
                )
                self.assertEqual(rebuilt.selection_id, selection.selection_id)

                before = destination.revision
                destination.import_json(document)
                self.assertEqual(len(destination.list_tracks()), 2)
                self.assertGreater(destination.revision, before)

            parsed = json.loads(document)
            parsed["schema_version"] = 999
            with CatalogStore(root / "reject.sqlite3") as destination:
                with self.assertRaisesRegex(CatalogFormatError, "schema_version"):
                    destination.import_json(json.dumps(parsed))
                self.assertEqual(destination.list_tracks(), ())

    def test_general_import_rejects_tampered_ids_and_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "source.sqlite3") as source:
                source.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                parsed = json.loads(source.export_json())

            parsed["tracks"][0]["track_id"] = "trk_sha256_" + ("0" * 64)
            with CatalogStore(root / "destination.sqlite3") as destination:
                with self.assertRaisesRegex(ValueError, "does not match"):
                    destination.import_json(json.dumps(parsed))
                self.assertEqual(destination.list_tracks(), ())

            parsed["tracks"][0]["sha256"] = "0" * 64
            parsed["tracks"][0]["unexpected"] = "field"
            with CatalogStore(root / "destination-2.sqlite3") as destination:
                with self.assertRaisesRegex(CatalogFormatError, "unknown field"):
                    destination.import_json(json.dumps(parsed))

    def test_manifest_ingestion_reopens_and_indexes_inputs_outputs_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_path = _write(root / "target.wav", b"target")
            reference_path = _write(root / "reference.wav", b"reference")
            output_path = _write(root / "master.wav", b"master")
            event_path = _write(root / "events.jsonl", b"{}\n")
            manifest_path = root / "manifest.json"

            manifest = RunManifest.create(run_id="ingested-run")
            manifest.add_input(
                ArtifactManifest.from_file(target_path, role="target", media_type="audio/wav")
            )
            manifest.add_input(
                ArtifactManifest.from_file(
                    reference_path,
                    role="reference[0]",
                    media_type="audio/wav",
                )
            )
            manifest.add_output(
                ArtifactManifest.from_file(
                    output_path,
                    role="mastered-output",
                    media_type="audio/wav",
                )
            )
            manifest.add_output(
                ArtifactManifest.from_file(
                    event_path,
                    role="event-log",
                    media_type="application/x-ndjson",
                )
            )
            manifest.mark_completed()
            manifest.save(manifest_path)

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                target = catalog.add_track(target_path, roles=(CatalogRole.TARGET,))
                reference = catalog.add_track(
                    reference_path,
                    roles=(CatalogRole.REFERENCE,),
                )
                selection = catalog.build_selection(
                    target.track_id,
                    reference_ids=(reference.track_id,),
                )
                run = catalog.ingest_manifest(manifest_path, selection=selection)
                self.assertEqual(run.status, "completed")
                self.assertIsNotNone(run.manifest_sha256)
                artifacts = catalog.list_run_artifacts("ingested-run")
                self.assertEqual(len(artifacts), 5)
                self.assertEqual(
                    sum(item.role is CatalogRole.AUDIT for item in artifacts),
                    2,
                )
                self.assertEqual(
                    {item.metadata["manifest_role"] for item in artifacts},
                    {
                        "target",
                        "reference[0]",
                        "mastered-output",
                        "event-log",
                        "run-manifest",
                    },
                )

                output_path.write_bytes(b"tampered")
                with self.assertRaisesRegex(CatalogConflictError, "changed"):
                    catalog.ingest_manifest(manifest_path, selection=selection)
                self.assertEqual(len(catalog.list_run_artifacts("ingested-run")), 5)

    def test_future_and_unversioned_databases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            future = root / "future.sqlite3"
            connection = sqlite3.connect(future)
            try:
                connection.execute("PRAGMA user_version = 999")
            finally:
                connection.close()
            with self.assertRaisesRegex(CatalogFormatError, "newer"):
                CatalogStore(future)

            unknown = root / "unknown.sqlite3"
            connection = sqlite3.connect(unknown)
            try:
                connection.execute("CREATE TABLE mystery (value TEXT)")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(CatalogFormatError, "unversioned"):
                CatalogStore(unknown)

    def test_public_model_validation_rejects_ambiguous_or_unsafe_documents(self) -> None:
        digest_a = "a" * 64
        digest_b = "b" * 64
        target = SelectedTrack(
            track_id=f"trk_sha256_{digest_a}",
            sha256=digest_a,
            size_bytes=10,
            path="target.wav",
        )
        reference_track = SelectedTrack(
            track_id=f"trk_sha256_{digest_b}",
            sha256=digest_b,
            size_bytes=20,
            path="reference.wav",
        )
        reference = SelectedReference(
            track=reference_track,
            ordinal=0,
            level_weight=1,
            frequency_weight=1,
        )
        selection = CatalogSelection(target=target, references=(reference,))

        facts = AudioFacts.from_dict(
            {
                "sample_rate": 44_100,
                "channels": 2,
                "frames": 100,
                "duration_seconds": 0.1,
            }
        )
        self.assertEqual(facts.sample_rate, 44_100)
        for arguments in (
            (0, 2, 1, 1.0),
            (1, 0, 1, 1.0),
            (1, 1, -1, 1.0),
            (1, 1, 1, math.inf),
        ):
            with self.subTest(audio_facts=arguments):
                with self.assertRaises(ValueError):
                    AudioFacts(*arguments)

        with self.assertRaisesRegex(CatalogFormatError, "unknown field"):
            AudioFacts.from_dict(
                {
                    "sample_rate": 1,
                    "channels": 1,
                    "frames": 1,
                    "duration_seconds": 1.0,
                    "unexpected": True,
                }
            )
        with self.assertRaisesRegex(CatalogFormatError, "missing required"):
            AudioFacts.from_dict({"sample_rate": 1})
        with self.assertRaisesRegex(ValueError, "positive weight"):
            ReferenceSetMember(
                track_id=reference_track.track_id,
                ordinal=0,
                level_weight=0,
                frequency_weight=0,
            )
        with self.assertRaisesRegex(TypeError, "SelectedTrack"):
            SelectedReference(
                track="not-a-track",  # type: ignore[arg-type]
                ordinal=0,
                level_weight=1,
                frequency_weight=1,
            )
        with self.assertRaisesRegex(ValueError, "positive weight"):
            SelectedReference(
                track=reference_track,
                ordinal=0,
                level_weight=0,
                frequency_weight=0,
            )
        with self.assertRaisesRegex(TypeError, "selection.target"):
            CatalogSelection(
                target="bad",  # type: ignore[arg-type]
                references=(reference,),
            )
        with self.assertRaisesRegex(TypeError, "selection.references"):
            CatalogSelection(
                target=target,
                references=[reference],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "at least one reference"):
            CatalogSelection(target=target, references=())
        with self.assertRaisesRegex(ValueError, "contiguous"):
            CatalogSelection(
                target=target,
                references=(
                    SelectedReference(
                        track=reference_track,
                        ordinal=1,
                        level_weight=1,
                        frequency_weight=1,
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            CatalogSelection(
                target=target,
                references=(
                    reference,
                    SelectedReference(
                        track=reference_track,
                        ordinal=1,
                        level_weight=1,
                        frequency_weight=1,
                    ),
                ),
            )
        identical_selection = CatalogSelection(
            target=target,
            references=(
                SelectedReference(
                    track=target,
                    ordinal=0,
                    level_weight=1,
                    frequency_weight=1,
                ),
            ),
        )
        self.assertEqual(
            identical_selection.target.track_id,
            identical_selection.references[0].track.track_id,
        )
        zero_level = SelectedReference(
            track=reference_track,
            ordinal=0,
            level_weight=0,
            frequency_weight=1,
        )
        with self.assertRaisesRegex(ValueError, "total level"):
            CatalogSelection(target=target, references=(zero_level,))
        zero_frequency = SelectedReference(
            track=reference_track,
            ordinal=0,
            level_weight=1,
            frequency_weight=0,
        )
        with self.assertRaisesRegex(ValueError, "total frequency"):
            CatalogSelection(target=target, references=(zero_frequency,))

        for field, value, message in (
            ("kind", "other", "kind"),
            ("schema_version", 999, "schema_version"),
            ("selection_id", f"sel_sha256_{'0' * 64}", "does not match"),
        ):
            document = selection.to_dict()
            document[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(CatalogFormatError, message):
                    CatalogSelection.from_json(json.dumps(document))
        with self.assertRaisesRegex(CatalogFormatError, "root must be an object"):
            CatalogSelection.from_json("[]")
        with self.assertRaisesRegex(CatalogFormatError, "invalid JSON"):
            CatalogSelection.from_json("{")

    def test_store_conflicts_filters_and_required_run_artifact_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_path = _write(root / "target.wav", b"target")
            reference_path = _write(root / "reference.wav", b"reference")
            other_reference_path = _write(root / "reference-2.wav", b"reference-2")
            output_path = _write(root / "output.wav", b"output")
            with self.assertRaises(ValueError):
                CatalogStore(root / "bad.sqlite3", busy_timeout_ms=0)

            with CatalogStore(root / "catalog.sqlite3") as catalog:
                with self.assertRaises(ValueError):
                    catalog.add_track(target_path, roles=())
                with self.assertRaises(TypeError):
                    catalog.add_track(target_path, roles="target")
                with self.assertRaises(TypeError):
                    catalog.add_track(
                        target_path,
                        roles=(CatalogRole.TARGET,),
                        audio_facts="invalid",  # type: ignore[arg-type]
                    )

                facts = AudioFacts(44_100, 2, 100, 0.1)
                target = catalog.add_track(
                    target_path,
                    roles=(CatalogRole.TARGET,),
                    label="Target",
                    audio_facts=facts,
                )
                with self.assertRaisesRegex(CatalogConflictError, "audio facts"):
                    catalog.add_track(
                        target_path,
                        roles=(CatalogRole.TARGET,),
                        label="Target",
                        audio_facts=AudioFacts(48_000, 2, 100, 0.1),
                    )
                with self.assertRaisesRegex(CatalogConflictError, "label"):
                    catalog.add_track(
                        target_path,
                        roles=(CatalogRole.TARGET,),
                        label="Different",
                    )
                reference = catalog.add_track(
                    reference_path,
                    roles=(CatalogRole.REFERENCE,),
                )
                other_reference = catalog.add_track(
                    other_reference_path,
                    roles=(CatalogRole.REFERENCE,),
                )
                self.assertEqual(
                    catalog.list_tracks(role=CatalogRole.REFERENCE),
                    tuple(sorted((reference, other_reference), key=lambda item: item.track_id)),
                )
                with self.assertRaises(CatalogError):
                    catalog.get_track(f"trk_sha256_{'0' * 64}")

                reference_set = catalog.create_reference_set("Set")
                with self.assertRaises(CatalogConflictError):
                    catalog.create_reference_set("set")
                with self.assertRaisesRegex(CatalogConflictError, "reference role"):
                    catalog.set_reference_member(reference_set.set_id, target.track_id)
                catalog.set_reference_member(
                    reference_set.set_id,
                    reference.track_id,
                    ordinal=0,
                )
                with self.assertRaisesRegex(CatalogConflictError, "ordinal"):
                    catalog.set_reference_member(
                        reference_set.set_id,
                        other_reference.track_id,
                        ordinal=0,
                    )
                with self.assertRaises(CatalogError):
                    catalog.remove_reference_member(
                        reference_set.set_id,
                        other_reference.track_id,
                    )
                catalog.archive_track(other_reference.track_id)
                with self.assertRaisesRegex(CatalogConflictError, "archived"):
                    catalog.set_reference_member(
                        reference_set.set_id,
                        other_reference.track_id,
                    )

                with self.assertRaises(ValueError):
                    catalog.build_selection(target.track_id)
                with self.assertRaises(ValueError):
                    catalog.build_selection(
                        target.track_id,
                        reference_ids=(reference.track_id,),
                        reference_set_id=reference_set.set_id,
                    )
                selection = catalog.build_selection(
                    target.track_id,
                    reference_ids=(reference.track_id,),
                )
                catalog.record_run("run", "running", selection=selection)
                other_selection = CatalogSelection(
                    target=selection.target,
                    references=(
                        SelectedReference(
                            track=SelectedTrack(
                                track_id=other_reference.track_id,
                                sha256=other_reference.sha256,
                                size_bytes=other_reference.size_bytes,
                                path=str(other_reference_path),
                            ),
                            ordinal=0,
                            level_weight=1,
                            frequency_weight=1,
                        ),
                    ),
                )
                with self.assertRaisesRegex(CatalogConflictError, "immutable"):
                    catalog.record_run("run", "running", selection=other_selection)
                with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
                    catalog.record_run(
                        "other-run",
                        "completed",
                        manifest_sha256="bad",
                    )
                with self.assertRaises(CatalogError):
                    catalog.get_run("absent")

                with self.assertRaises(TypeError):
                    catalog.index_artifact(
                        "run",
                        output_path,
                        role=CatalogRole.GENERATED_OUTPUT,
                        metadata="bad",  # type: ignore[arg-type]
                    )
                with self.assertRaises(ValueError):
                    catalog.index_artifact(
                        "run",
                        output_path,
                        role=CatalogRole.GENERATED_OUTPUT,
                        state=LocationState.MISSING,
                    )
                with self.assertRaisesRegex(CatalogConflictError, "linked track"):
                    catalog.index_artifact(
                        "run",
                        output_path,
                        role=CatalogRole.GENERATED_OUTPUT,
                        track_id=target.track_id,
                    )
                catalog.index_artifact(
                    "run",
                    output_path,
                    role=CatalogRole.GENERATED_OUTPUT,
                    ordinal=3,
                )
                with self.assertRaisesRegex(CatalogConflictError, "already indexed"):
                    catalog.index_artifact(
                        "run",
                        output_path,
                        role=CatalogRole.GENERATED_OUTPUT,
                        ordinal=3,
                    )
                self.assertEqual(
                    len(
                        catalog.list_run_artifacts(
                            "run",
                            role=CatalogRole.GENERATED_OUTPUT,
                        )
                    ),
                    1,
                )

                reference_path.unlink()
                catalog.verify(reference.track_id)
                with self.assertRaisesRegex(CatalogConflictError, "no verified"):
                    catalog.preferred_location(reference.track_id)

    def test_export_referential_integrity_rejections_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CatalogStore(root / "source.sqlite3") as source:
                target = source.add_track(
                    _write(root / "target.wav", b"target"),
                    roles=(CatalogRole.TARGET,),
                )
                reference = source.add_track(
                    _write(root / "reference.wav", b"reference"),
                    roles=(CatalogRole.REFERENCE,),
                )
                reference_set = source.create_reference_set("Set")
                source.set_reference_member(reference_set.set_id, reference.track_id)
                selection = source.build_selection(
                    target.track_id,
                    reference_set_id=reference_set.set_id,
                )
                source.record_run("run", "completed", selection=selection)
                source.index_artifact(
                    "run",
                    _write(root / "output.wav", b"output"),
                    role=CatalogRole.GENERATED_OUTPUT,
                )
                baseline = json.loads(source.export_json())

            variants: list[tuple[str, dict[str, object]]] = []

            duplicate_track = deepcopy(baseline)
            duplicate_track["tracks"].append(deepcopy(duplicate_track["tracks"][0]))
            variants.append(("duplicate tracks", duplicate_track))

            absent_location_track = deepcopy(baseline)
            absent_location_track["locations"][0]["track_id"] = f"trk_sha256_{'0' * 64}"
            variants.append(("absent track", absent_location_track))

            duplicate_set = deepcopy(baseline)
            duplicate_set["reference_sets"].append(deepcopy(duplicate_set["reference_sets"][0]))
            variants.append(("duplicate reference sets", duplicate_set))

            absent_member = deepcopy(baseline)
            absent_member["reference_sets"][0]["members"][0]["track_id"] = f"trk_sha256_{'0' * 64}"
            variants.append(("absent track", absent_member))

            duplicate_run = deepcopy(baseline)
            duplicate_run["runs"].append(deepcopy(duplicate_run["runs"][0]))
            variants.append(("duplicate runs", duplicate_run))

            absent_artifact_run = deepcopy(baseline)
            absent_artifact_run["artifacts"][0]["run_id"] = "absent"
            variants.append(("absent run", absent_artifact_run))

            bad_artifact = deepcopy(baseline)
            bad_artifact["artifacts"][0]["artifact_id"] = f"art_sha256_{'0' * 64}"
            variants.append(("does not match", bad_artifact))

            relative_location = deepcopy(baseline)
            relative_location["locations"][0]["path"] = "relative.wav"
            variants.append(("must be absolute", relative_location))

            bad_timestamp = deepcopy(baseline)
            bad_timestamp["tracks"][0]["created_at"] = "not-a-time"
            variants.append(("ISO 8601", bad_timestamp))

            for index, (message, variant) in enumerate(variants):
                with self.subTest(message=message):
                    with CatalogStore(root / f"destination-{index}.sqlite3") as destination:
                        with self.assertRaisesRegex(
                            (CatalogFormatError, ValueError),
                            message,
                        ):
                            destination.import_json(json.dumps(variant))
                        self.assertEqual(destination.list_tracks(), ())


if __name__ == "__main__":
    unittest.main()
