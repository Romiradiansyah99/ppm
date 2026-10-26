"""Definition registry and app-spec validation.

The taxonomy is deliberately small: stages, categories, formats. Rate/area
definitions are out of scope until the rate phase starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDERS = {"", "unassigned", "tbd", "todo", "<nama user>", "<nama owner>", "nama user", "nama owner"}

REQUIRED_APP_KEYS = (
    "app", "version", "user", "owner", "data_sources", "permission_rule",
    "views", "actions", "notifications", "memory_scope",
    "deprecation_condition", "failsafe",
)


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Taxonomy:
    stages: tuple[str, ...]
    categories: tuple[str, ...]
    formats: tuple[str, ...]
    signed_by: str | None
    status: str


def load_taxonomy(path: str | Path) -> Taxonomy:
    taxonomy_path = Path(path)
    if not taxonomy_path.is_file():
        raise RegistryError(f"no taxonomy at {taxonomy_path}")
    data = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8")) or {}
    stages = tuple(data.get("stages") or ())
    categories = tuple(data.get("categories") or ())
    formats = tuple(data.get("formats") or ())
    if not stages or not categories or not formats:
        raise RegistryError("taxonomy stages, categories and formats must all be non-empty")
    return Taxonomy(
        stages=stages,
        categories=categories,
        formats=formats,
        signed_by=data.get("signed_by"),
        status=str(data.get("status", "DRAFT")),
    )


def validate_taxonomy(taxonomy: Taxonomy, strict: bool = False) -> list[str]:
    problems: list[str] = []
    if not taxonomy.signed_by or taxonomy.signed_by.strip().lower() in PLACEHOLDERS:
        problems.append("taxonomy is not signed: set signed_by to a named person")
    if taxonomy.status.upper() != "SIGNED":
        problems.append(f"taxonomy status is {taxonomy.status}, expected SIGNED")
    if strict and problems:
        raise RegistryError("; ".join(problems))
    return problems


def load_app_spec(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: app spec must be a mapping")
    return data


def validate_app_spec(spec: dict[str, Any], taxonomy: Taxonomy) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED_APP_KEYS:
        if key not in spec:
            problems.append(f"{spec.get('app', '<unknown>')}: missing key '{key}'")
    for key in ("user", "owner"):
        value = str(spec.get(key, "")).strip().lower()
        if value in PLACEHOLDERS:
            problems.append(f"{spec.get('app', '<unknown>')}: '{key}' is a placeholder")
    app = str(spec.get("app", "")).strip()
    if not app:
        problems.append("app name is empty")
    if not isinstance(spec.get("views", []), list):
        problems.append(f"{app}: views must be a list")
    for view in spec.get("views", []) or []:
        if not isinstance(view, dict) or "name" not in view:
            problems.append(f"{app}: every view needs a name")
    for action in spec.get("actions", []) or []:
        if not isinstance(action, dict):
            problems.append(f"{app}: actions must be mappings")
            continue
        gate = action.get("approval_gate", "none")
        if gate not in {"none", "peer", "owner", "partner"}:
            problems.append(f"{app}: invalid approval_gate '{gate}'")
    stage_filters = [view.get("filters", []) for view in spec.get("views", []) or []]
    for filters in stage_filters:
        for field in filters or []:
            if field in {"stage", "format", "category"}:
                continue
    return problems


def validate_app_specs(directory: str | Path, taxonomy: Taxonomy, strict: bool = False) -> list[str]:
    problems: list[str] = []
    specs = sorted(Path(directory).glob("*.yaml"))
    if not specs:
        problems.append(f"no app specs in {directory}")
    for path in specs:
        problems.extend(validate_app_spec(load_app_spec(path), taxonomy))
    if strict and problems:
        raise RegistryError("; ".join(problems))
    return problems
