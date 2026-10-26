from pathlib import Path

import pytest

from ppm.registry import (
    RegistryError,
    load_taxonomy,
    validate_app_spec,
    validate_app_specs,
    validate_taxonomy,
)


def test_unsiged_taxonomy_warns():
    taxonomy = load_taxonomy(Path(__file__).resolve().parents[1] / "registry" / "template_taxonomy.yaml")
    problems = validate_taxonomy(taxonomy, strict=False)
    assert any("not signed" in problem for problem in problems)
    with pytest.raises(RegistryError):
        validate_taxonomy(taxonomy, strict=True)


def test_app_spec_placeholder_user_is_flagged(tmp_path):
    taxonomy = load_taxonomy(Path(__file__).resolve().parents[1] / "registry" / "template_taxonomy.yaml")
    spec = {
        "app": "x", "version": 1, "user": "<nama user>", "owner": "<nama owner>",
        "data_sources": [], "permission_rule": "x", "views": [], "actions": [],
        "notifications": [], "memory_scope": "private",
        "deprecation_condition": "x", "failsafe": "x",
    }
    problems = validate_app_spec(spec, taxonomy)
    assert any("'user' is a placeholder" in problem for problem in problems)
    assert any("'owner' is a placeholder" in problem for problem in problems)


def test_repo_app_specs_have_no_structural_problems():
    root = Path(__file__).resolve().parents[1]
    taxonomy = load_taxonomy(root / "registry" / "template_taxonomy.yaml")
    problems = validate_app_specs(root / "app_specs", taxonomy, strict=False)
    assert not any("missing key" in problem for problem in problems)
