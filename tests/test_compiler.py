"""The spec compiler (Phase 6): views -> SQL, draft validation, human
approval, and the whole-spec compile."""

from __future__ import annotations

import yaml
import pytest

from ppm.appspec import AppView, load_app_spec
from ppm.compiler import (
    CompileError,
    _slug,
    approve_draft,
    compile_app,
    compile_view,
    save_draft,
    validate_draft,
)
from ppm.config import REPO_ROOT
from ppm.models import _StubAppSpecDrafter


def test_compile_view_with_filters_and_sort(registry):
    spec = load_app_spec(REPO_ROOT / "app_specs" / "rate-lookup.yaml", registry)
    view = spec.view("search")
    query, params = compile_view(view, registry, filters={"sector": "commercial"}, limit=25)
    text = query.as_string(None)
    assert "SELECT" in text and 'FROM "benchmark_rate"' in text
    assert 'WHERE "sector" = %s' in text
    assert 'ORDER BY "date_normalised_to" DESC' in text
    assert params == ["commercial", 25]


def test_compile_view_rejects_undeclared_filter(registry):
    spec = load_app_spec(REPO_ROOT / "app_specs" / "rate-lookup.yaml", registry)
    with pytest.raises(CompileError, match="not declared"):
        compile_view(spec.view("search"), registry, filters={"status": "active"})


def test_compile_view_all_excludes_sensitive_fields(registry):
    view = AppView(name="people", entity="Person", fields="all", filters=[], sort=[])
    query, _ = compile_view(view, registry)
    text = query.as_string(None)
    assert "SELECT" in text and "display_name" in text
    assert "password_hash" not in text


def test_compile_view_rejects_unknown_sort_column(registry):
    view = AppView(name="x", entity="BenchmarkRate", fields="all", filters=[], sort=["nope desc"])
    with pytest.raises(CompileError, match="sort column"):
        compile_view(view, registry)


def test_stub_drafter_produces_a_valid_spec(registry, tmp_path):
    from ppm.compiler import registry_summary

    drafter = _StubAppSpecDrafter()
    text = drafter.draft("I want to log variations against each project", registry_summary(registry))
    draft = save_draft(text, "variation-log", tmp_path)
    assert draft.exists()
    issues = validate_draft(draft, registry)
    assert issues == [], issues


def test_unknown_entity_draft_is_rejected(registry, tmp_path):
    bad = """
app: broken-app
version: 1
user: UNASSIGNED
owner: nobody
data_sources: [MoonBase]
permission_rule: none
views:
  - name: v
    entity: MoonBase
    fields: all
    filters: []
    sort: []
actions: []
notifications: []
memory_scope: shared
deprecation_condition: never
failsafe: n/a
"""
    draft = save_draft(bad, "broken-app", tmp_path)
    issues = validate_draft(draft, registry)
    assert any("MoonBase" in issue for issue in issues)
    with pytest.raises(CompileError, match="does not compile"):
        approve_draft(draft, "tester", app_specs_dir=tmp_path / "approved", drafts_dir=tmp_path)


def test_approve_moves_the_draft_into_app_specs(registry, tmp_path):
    from ppm.compiler import registry_summary

    drafts = tmp_path / "drafts"
    approved = tmp_path / "app_specs"
    approved.mkdir()
    text = _StubAppSpecDrafter().draft("watch RAID entries", registry_summary(registry))
    draft = save_draft(text, "risk-watch", drafts)
    target = approve_draft(draft, "romi", app_specs_dir=approved, drafts_dir=drafts, registry=registry)
    assert target.name == "risk-watch.yaml"
    assert target.exists() and not draft.exists()

    # approving the same name again refuses (no silent overwrite)
    draft2 = save_draft(text, "risk-watch", drafts)
    with pytest.raises(CompileError, match="already exists"):
        approve_draft(draft2, "romi", app_specs_dir=approved, drafts_dir=drafts, registry=registry)


def test_compile_app_end_to_end(registry):
    compiled = compile_app(REPO_ROOT / "app_specs" / "knowledge.yaml", registry)
    assert compiled["spec"].name == "knowledge"
    assert "lesson.submit" in compiled["actions"]
    assert "SELECT" in compiled["views"]["shared_lessons"]


def test_slug_is_safe():
    assert _slug("Variation Log!! 2026") == "variation-log-2026"
