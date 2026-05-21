"""Tests for accelerator/generators/manifest_schema.py"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "accelerator" / "generators"))

import manifest_schema  # noqa: E402


def _minimal_manifest() -> dict:
    """Smallest manifest that satisfies the schema."""
    return {
        "specChecksum": "abc123",
        "buildTimestamp": "2026-05-18T00:00:00.000000Z",
        "customer": {"slug": "acme", "name": "Acme Co"},
        "deployment": {
            "environmentName": "acme-prototype",
            "resourceGroup": "rg-acme-prototype",
            "location": "eastus2",
            "suffix": "abcd",
            "customerShort": "acme",
            "demoTheme": "acme",
        },
        "resources": {
            "cosmosAccountName": "acme-cosmos",
            "cosmosDatabaseName": "acme-db",
            "searchIndexName": "acme-index",
            "storageAccount": "acmest",
            "miName": "acme-id",
            "acrName": "acmeacr",
        },
        "branding": {
            "agentName": "Acme Bot",
            "primaryColor": "#000",
            "accentColor": "#fff",
            "useCaseTitle": "Demo",
            "starterQuestions": ["q1"],
            "personaName": "Persona",
            "personaRole": "Role",
        },
        "tables": [{"name": "t1", "partitionKey": "/k", "seedCount": 10}],
        "agents": [{"name": "Agent", "model": "gpt-4o", "tools": []}],
        "modelDeployments": [
            {"deploymentName": "gpt-4o", "model": "gpt-4o", "capacity": 10}
        ],
        "documents": ["Doc"],
        "foundryIq": {"indexName": "acme-index"},
        "aiLocation": "eastus2",
    }


class TestManifestSchema(unittest.TestCase):
    def test_minimal_manifest_validates(self):
        self.assertEqual(manifest_schema.validate(_minimal_manifest()), [])

    def test_missing_required_top_level_field(self):
        m = _minimal_manifest()
        del m["specChecksum"]
        errors = manifest_schema.validate(m)
        self.assertTrue(any("specChecksum" in e for e in errors), errors)

    def test_wrong_type_at_nested_path(self):
        m = _minimal_manifest()
        m["deployment"]["location"] = 123
        errors = manifest_schema.validate(m)
        self.assertTrue(any("deployment.location" in e for e in errors), errors)

    def test_empty_required_string(self):
        m = _minimal_manifest()
        m["customer"]["slug"] = ""
        errors = manifest_schema.validate(m)
        self.assertTrue(any("customer.slug" in e for e in errors), errors)

    def test_agent_model_must_match_deployment(self):
        m = _minimal_manifest()
        m["agents"][0]["model"] = "not-a-real-deployment"
        errors = manifest_schema.validate(m)
        self.assertTrue(
            any("not in modelDeployments" in e for e in errors), errors
        )

    def test_list_item_missing_required_field(self):
        m = _minimal_manifest()
        del m["tables"][0]["partitionKey"]
        errors = manifest_schema.validate(m)
        self.assertTrue(any("partitionKey" in e for e in errors), errors)

    def test_load_and_validate_round_trip(self):
        m = _minimal_manifest()
        tmp = pathlib.Path(self.id() + ".json")
        try:
            tmp.write_text(json.dumps(m), encoding="utf-8")
            loaded = manifest_schema.load_and_validate(tmp)
            self.assertEqual(loaded["customer"]["slug"], "acme")
        finally:
            if tmp.exists():
                tmp.unlink()


if __name__ == "__main__":
    unittest.main()
