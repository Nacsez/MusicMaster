"""Regression checks for the editor-facing job JSON Schema and examples."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from music_mastering_tools.config import EngineKind, load_job_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "job-config-v1.schema.json"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


class JobSchemaDocumentTests(unittest.TestCase):
    def test_schema_encodes_current_reference_and_weight_boundaries(self) -> None:
        schema = json.loads(
            SCHEMA_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )

        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        references = schema["properties"]["references"]
        self.assertEqual(references["minItems"], 1)
        self.assertEqual(references["maxItems"], 32)
        self.assertEqual(len(references["allOf"]), 2)
        self.assertTrue(all(rule["minContains"] == 1 for rule in references["allOf"]))

        engine_rule = schema["allOf"][0]
        self.assertEqual(
            engine_rule["if"]["properties"]["execution"]["properties"]["engine"]["const"],
            EngineKind.NATIVE.value,
        )
        self.assertEqual(
            engine_rule["then"]["properties"]["references"]["maxItems"],
            32,
        )
        upstream_references = engine_rule["else"]["properties"]["references"]
        self.assertEqual(upstream_references["maxItems"], 1)
        upstream_weights = upstream_references["items"]["allOf"][1]["properties"]
        self.assertEqual(upstream_weights["level_weight"]["const"], 1.0)
        self.assertEqual(upstream_weights["frequency_weight"]["const"], 1.0)

    def test_every_committed_example_matches_the_typed_structural_contract(self) -> None:
        examples = {
            "native-target.json": (EngineKind.NATIVE, 2),
            "upstream-baseline.json": (EngineKind.UPSTREAM, 1),
            "weighted-references.json": (EngineKind.NATIVE, 2),
        }
        self.assertEqual(
            {path.name for path in (PROJECT_ROOT / "configs").glob("*.json")},
            set(examples),
        )
        for name, (engine, reference_count) in examples.items():
            with self.subTest(config=name):
                job = load_job_config(PROJECT_ROOT / "configs" / name)
                self.assertIs(job.execution.engine, engine)
                self.assertEqual(len(job.references), reference_count)
                self.assertAlmostEqual(sum(job.normalized_level_weights), 1.0)
                self.assertAlmostEqual(sum(job.normalized_frequency_weights), 1.0)


if __name__ == "__main__":
    unittest.main()
