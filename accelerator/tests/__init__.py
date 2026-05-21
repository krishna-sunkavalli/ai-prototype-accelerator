"""
Tests for the deterministic build pipeline.

These tests run without Azure access. They cover:
    - manifest_schema rejects malformed manifests
    - model_catalog version resolution
    - fill-templates produces non-empty, placeholder-free output for a
      synthesized manifest
    - sentinels.is_stale reacts correctly to spec/output changes
    - register_agents tool catalogue parses from tool_definitions.yaml

Run from repo root:
    py -3 -m unittest discover -s accelerator/tests -v
"""
