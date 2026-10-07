"""App spec loading and registry cross-validation."""

from __future__ import annotations

import pytest
import yaml

from ppm.appspec import AppSpecError, load_app_spec
from ppm.config import REPO_ROOT


def test_shipped_rate_lookup_spec_loads(registry):
    spec = load_app_spec(REPO_ROOT / "app_specs" / "rate-lookup.yaml", registry)
    assert spec.name == "rate-lookup"
    assert spec.actions == []                       # read-only in Phase 0
    assert spec.memory_scope == "private"
    assert "90 consecutive days" in spec.deprecation_condition
    assert "unclassified" in spec.failsafe
    assert spec.user == "UNASSIGNED"                # deliberately: no name, no start
    search = spec.view("search")
    assert "sector" in search.filters
    assert search.entity == "BenchmarkRate"


def test_unknown_field_fails_validation(registry, tmp_path):
    spec = yaml.safe_load((REPO_ROOT / "app_specs" / "rate-lookup.yaml").read_text(encoding="utf-8"))
    spec["views"][0]["fields"].append("made_up_field")
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(AppSpecError, match="not in registry"):
        load_app_spec(path, registry)


def test_unknown_data_source_fails_validation(registry, tmp_path):
    spec = yaml.safe_load((REPO_ROOT / "app_specs" / "rate-lookup.yaml").read_text(encoding="utf-8"))
    spec["data_sources"].append("Mystery")
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(AppSpecError, match="data source"):
        load_app_spec(path, registry)


def test_missing_keys_rejected(registry, tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("app: x\n", encoding="utf-8")
    with pytest.raises(AppSpecError, match="missing keys"):
        load_app_spec(path, registry)
