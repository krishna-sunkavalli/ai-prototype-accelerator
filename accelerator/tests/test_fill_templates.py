"""Tests for accelerator/generators/fill-templates.py — end-to-end hydration.

Synthesizes a minimal valid manifest, runs fill-templates as a subprocess
against a temporary workspace, and asserts the resulting files contain no
unresolved {{PLACEHOLDER}} tokens.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILL = ROOT / "accelerator" / "generators" / "fill-templates.py"
TEMPLATES = ROOT / "accelerator" / "templates" / "prototype"

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def _manifest_for(workspace: pathlib.Path) -> dict:
    return {
        "specChecksum": "test1234",
        "buildTimestamp": "2026-05-18T00:00:00.000000Z",
        "customer": {"slug": "acme", "name": "Acme Co", "industry": "Test"},
        "deployment": {
            "environmentName": "acme-prototype",
            "resourceGroup": "rg-acme",
            "location": "eastus2",
            "suffix": "abcd",
            "customerShort": "acme",
            "demoTheme": "acme",
        },
        "resources": {
            "cosmosAccountName": "acme-cosmos",
            "cosmosDatabaseName": "acme-db",
            "searchIndexName": "acme-idx",
            "storageAccount": "acmest",
            "miName": "acme-id",
            "acrName": "acmeacr",
        },
        "branding": {
            "agentName": "Acme Bot",
            "primaryColor": "#000000",
            "accentColor": "#ffffff",
            "fontFamily": "Arial",
            "logoUrl": "",
            "welcomeMessage": "hello",
            "useCaseTitle": "Demo Use Case",
            "starterQuestions": ["q1", "q2"],
            "personaName": "Persona",
            "personaRole": "Role",
        },
        "tables": [
            {"name": "t1", "partitionKey": "/k", "seedCount": 10, "seedScenario": "", "description": "", "columns": []},
        ],
        "agents": [
            {"name": "TestAgent", "model": "gpt-4o", "tools": ["run_sql_query"], "role": "Test specialist"},
        ],
        "modelDeployments": [
            {"deploymentName": "gpt-4o", "model": "gpt-4o", "version": "2024-11-20", "capacity": 10},
        ],
        "documents": ["Onboarding Guide"],
        "documentSpecs": [{"title": "Onboarding Guide", "description": "", "key_topics": []}],
        "foundryIq": {"indexName": "acme-idx", "chunkSize": 1024, "overlap": 128},
        "mockApiEnabled": False,
        "mockApiEndpoints": [],
        "aiLocation": "eastus2",
    }


class TestFillTemplates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = pathlib.Path(self.tmp.name)
        # Layout fill-templates expects: <ws>/accelerator/{generators,templates}/...
        shutil.copytree(
            ROOT / "accelerator" / "generators",
            self.ws / "accelerator" / "generators",
        )
        shutil.copytree(TEMPLATES, self.ws / "accelerator" / "templates" / "prototype")
        state = self.ws / "generated" / "build-state"
        state.mkdir(parents=True)
        (state / "manifest.json").write_text(
            json.dumps(_manifest_for(self.ws)), encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_hydration_emits_placeholder_free_output(self):
        result = subprocess.run(
            [sys.executable, str(self.ws / "accelerator" / "generators" / "fill-templates.py")],
            capture_output=True,
            text=True,
            cwd=str(self.ws),
        )
        self.assertEqual(
            result.returncode,
            0,
            f"fill-templates failed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        for rel in [
            "infra/main.bicepparam",
            "azure.yaml",
            "backend/config.py",
            "hooks/postprovision.sh",
            "hooks/postprovision.ps1",
        ]:
            out = self.ws / "generated" / "prototype" / rel
            self.assertTrue(out.exists(), f"missing output: {rel}")
            text = out.read_text(encoding="utf-8")
            hits = sorted(set(PLACEHOLDER_RE.findall(text)))
            self.assertEqual(hits, [], f"{rel} has unresolved placeholders: {hits}")

    def test_unfilled_placeholder_causes_nonzero_exit(self):
        # Inject a bogus placeholder into the bicepparam template copy.
        tpl = self.ws / "accelerator" / "templates" / "prototype" / "infra" / "main.bicepparam.tpl"
        tpl.write_text(tpl.read_text(encoding="utf-8") + "\n// {{BOGUS_PLACEHOLDER}}\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(self.ws / "accelerator" / "generators" / "fill-templates.py")],
            capture_output=True,
            text=True,
            cwd=str(self.ws),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BOGUS_PLACEHOLDER", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
