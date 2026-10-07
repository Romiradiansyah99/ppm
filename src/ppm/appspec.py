"""App specs (plan section 4): YAML documents that describe an app; Phase 0
implements the read-only subset - views drive the retrieval layer and the UI,
actions are empty. The full YAML -> StateGraph compiler is Phase 6.

Spec validation runs against the definition registry so a view can only name
fields the entity actually masters (the plan's failsafe: a broken spec fails
validation, it never reaches the user as a stack trace).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ppm.registry import Registry, get_registry

REQUIRED_KEYS = ("app", "version", "user", "owner", "data_sources", "views",
                 "actions", "memory_scope", "deprecation_condition", "failsafe")


class AppSpecError(Exception):
    pass


@dataclass(frozen=True)
class AppView:
    name: str
    entity: str
    fields: list[str] | str          # list or "all"
    filters: list[str]
    sort: list[str]
    provenance: list[str] | None = None


@dataclass(frozen=True)
class AppSpec:
    name: str
    version: int
    user: str
    owner: str
    data_sources: list[str]
    permission_rule: str | None
    views: list[AppView]
    actions: list[dict]
    memory_scope: str
    deprecation_condition: str
    failsafe: str
    path: str

    def view(self, name: str) -> AppView:
        for view in self.views:
            if view.name == name:
                return view
        raise AppSpecError(f"{self.name}: no view '{name}'")


def load_app_spec(path: str | Path, registry: Registry | None = None) -> AppSpec:
    spec_path = Path(path)
    if not spec_path.is_file():
        raise AppSpecError(f"no app spec at {spec_path}")
    raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AppSpecError(f"{spec_path.name}: not a mapping")
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise AppSpecError(f"{spec_path.name}: missing keys {missing}")

    registry = registry or get_registry()
    warnings = validate_against_registry(raw, registry)
    error_warnings = [w for w in warnings if w.startswith("ERROR")]
    if error_warnings:
        raise AppSpecError(f"{spec_path.name}: " + "; ".join(error_warnings))

    views = []
    for view in raw["views"]:
        views.append(AppView(
            name=view["name"],
            entity=view["entity"],
            fields=view.get("fields", "all"),
            filters=view.get("filters", []),
            sort=view.get("sort", []),
            provenance=view.get("provenance"),
        ))

    return AppSpec(
        name=raw["app"],
        version=int(raw["version"]),
        user=str(raw["user"]),
        owner=str(raw["owner"]),
        data_sources=[str(s) for s in raw["data_sources"]],
        permission_rule=raw.get("permission_rule"),
        views=views,
        actions=list(raw.get("actions") or []),
        memory_scope=str(raw["memory_scope"]),
        deprecation_condition=str(raw["deprecation_condition"]),
        failsafe=str(raw["failsafe"]),
        path=str(spec_path),
    )


def validate_against_registry(raw: dict, registry: Registry) -> list[str]:
    """Returns warnings; entries starting with ERROR fail the load."""
    issues: list[str] = []
    known = set(registry.entities)
    registry_entity: dict[str, str] = {}
    for name in known:
        registry_entity[name.lower()] = name
        registry_entity[name.lower().replace("_", "")] = name   # BenchmarkRate -> benchmarkrate

    def entity_key(value: object) -> str | None:
        return registry_entity.get(str(value).lower().replace("_", "").replace("-", "").replace(" ", ""))

    for source in raw.get("data_sources", []):
        if entity_key(source) is None:
            issues.append(f"ERROR data source '{source}' not in the registry")

    if raw.get("user") in (None, "", "UNASSIGNED"):
        issues.append("WARNING app has no named user - plan section 4: no name, no build")

    if not raw.get("deprecation_condition"):
        issues.append("ERROR missing deprecation_condition")
    if not raw.get("failsafe"):
        issues.append("ERROR missing failsafe")

    for view in raw.get("views", []):
        entity_key_name = entity_key(view.get("entity", ""))
        if entity_key_name is None:
            issues.append(f"ERROR view '{view.get('name')}' names unknown entity '{view.get('entity')}'")
            continue
        fields = view.get("fields")
        if isinstance(fields, list):
            known_fields = set(registry.entity(entity_key_name).get("fields", {}))
            unknown = [f for f in fields if f not in known_fields]
            if unknown:
                issues.append(f"ERROR view '{view.get('name')}' fields not in registry: {unknown}")
    return issues
