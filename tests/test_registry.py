"""Definition registry: loading, versioning, unit canonicalisation, the
unresolved-definition report."""

from __future__ import annotations

import shutil

import pytest
import yaml

from ppm.registry import RegistryError, load_registry


def test_phase0_entities_present(registry):
    assert {"document", "project", "cost_plan", "benchmark_rate"} <= set(registry.entities)


def test_version_is_content_hash(registry):
    assert len(registry.version) == 12
    assert registry.version == load_registry().version


def test_version_changes_when_content_changes(tmp_path):
    from ppm.config import REPO_ROOT

    original = load_registry()
    shutil.copytree(REPO_ROOT / "registry", tmp_path / "registry")
    changed = tmp_path / "registry" / "entities" / "benchmark_rate.yaml"
    doc = yaml.safe_load(changed.read_text(encoding="utf-8"))
    doc["registry_version"] = 2
    changed.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert load_registry(tmp_path / "registry").version != original.version


def test_missing_keys_rejected(tmp_path):
    (tmp_path / "entities").mkdir(parents=True)
    (tmp_path / "entities" / "project.yaml").write_text("entity: project\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="missing keys"):
        load_registry(tmp_path)


def test_filename_mismatch_rejected(tmp_path):
    (tmp_path / "entities").mkdir(parents=True)
    (tmp_path / "entities" / "wrong.yaml").write_text(
        "entity: project\ntable: project\nregistry_version: 1\nstatus: X\nowner: y\n", encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="does not match filename"):
        load_registry(tmp_path)


def test_unit_canonicalisation(registry):
    for raw, expected in [
        ("m2", "m2"), ("m²", "m2"), ("SQM", "m2"), ("sq.m", "m2"),
        ("M3", "m3"), ("nr", "nr"), ("No", "nr"), ("NR", "nr"),
        ("sum", "sum"), ("lump sum", "sum"), ("kg", "kg"), ("ton", "ton"),
    ]:
        assert registry.canonical_unit(raw) == expected, raw
    assert registry.canonical_unit("banana") is None
    assert registry.canonical_unit(None) is None


def test_element_codes_unresolved_policy(registry):
    assert not registry.element_codes_resolved()
    assert registry.unknown_code_severity() == "warning"
    assert not registry.is_known_element_code("C20")


def test_unresolved_definitions_reported(registry):
    unresolved = "\n".join(registry.unresolved_definitions())
    assert "benchmark_rate.element_codes" in unresolved
    assert "cost_plan.area_basis" in unresolved
    assert "cost_plan.rate_basis" in unresolved


def test_redaction_blocklist_loaded(registry):
    assert "PT SYNTHETIC CLIENT" in registry.redaction_blocklist()


def test_enum_validation(registry):
    assert registry.is_valid_enum("project", "sector", "residential")
    assert not registry.is_valid_enum("project", "sector", "space-station")
    assert not registry.is_valid_enum("project", "sector", None)
    # no enum declared -> anything passes
    assert registry.is_valid_enum("benchmark_rate", "description", "anything")
