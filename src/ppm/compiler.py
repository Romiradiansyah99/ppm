"""The spec compiler (plan section 6, Phase 6).

Views compile to SQL here; actions compile to LangGraph workflows in
ppm.workflows.actions. AI-authored drafts land in app_specs_drafts/ and only
become apps when a human runs `ppm apps approve` after validation against the
definition registry and the action compiler.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml
from psycopg import sql

from ppm import db
from ppm.appspec import AppSpecError, AppView, load_app_spec
from ppm.config import REPO_ROOT, Settings, get_settings
from ppm.registry import Registry, get_registry
from ppm.workflows.actions import load_actions, parse_app_actions

SENSITIVE_FIELDS = {"password_hash"}
DRAFTS_DIR = REPO_ROOT / "app_specs_drafts"


class CompileError(Exception):
    pass


# --- views -> SQL ------------------------------------------------------------------

def compile_view(view: AppView, registry: Registry, filters: dict | None = None,
                 limit: int = 200) -> tuple[sql.SQL, list]:
    entity_name = registry.resolve(view.entity)
    if entity_name is None:
        raise CompileError(f"view '{view.name}': unknown entity '{view.entity}'")
    entity = registry.entity(entity_name)
    field_names = list(entity.get("fields", {}))

    if isinstance(view.fields, list):
        columns = [f for f in view.fields if f not in SENSITIVE_FIELDS]
        unknown = [c for c in columns if c not in field_names]
        if unknown:
            raise CompileError(f"view '{view.name}': fields not in registry: {unknown}")
    else:
        columns = [f for f in field_names if f not in SENSITIVE_FIELDS]

    filters = filters or {}
    declared = set(view.filters or [])
    undeclared = [key for key, value in filters.items() if value not in (None, "") and key not in declared]
    if undeclared:
        raise CompileError(f"view '{view.name}': filters not declared: {undeclared}")

    clauses, params = [], []
    for key, value in filters.items():
        if value in (None, ""):
            continue
        if key not in field_names:
            raise CompileError(f"view '{view.name}': filter '{key}' is not a registry field")
        clauses.append(sql.SQL("{} = %s").format(sql.Identifier(key)))
        params.append(value)

    order_parts = []
    for sort in view.sort or []:
        pieces = str(sort).split()
        column = pieces[0]
        direction = "DESC" if len(pieces) > 1 and pieces[1].lower().startswith("desc") else "ASC"
        if column not in field_names:
            raise CompileError(f"view '{view.name}': sort column '{column}' is not a registry field")
        order_parts.append(sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(direction)))

    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(entity["table"])
    )
    if clauses:
        query = query + sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
    if order_parts:
        query = query + sql.SQL(" ORDER BY ") + sql.SQL(", ").join(order_parts)
    query = query + sql.SQL(" LIMIT %s")
    params.append(limit)
    return query, params


def run_view(view: AppView, registry: Registry, filters: dict | None = None,
             person_id: str | None = None, limit: int = 200) -> list[dict]:
    query, params = compile_view(view, registry, filters=filters, limit=limit)
    from psycopg.rows import dict_row

    if person_id:
        with db.connection_app(person_id) as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchall()
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, params).fetchall()


# --- registry summary for AI authoring -------------------------------------------------

def registry_summary(registry: Registry | None = None, max_fields: int = 14) -> str:
    registry = registry or get_registry()
    lines = []
    for name, entity in sorted(registry.entities.items()):
        fields = [f for f in entity.get("fields", {}) if f not in SENSITIVE_FIELDS][:max_fields]
        line = f"- {name} ({entity.get('table')}): {', '.join(fields)}"
        enums = entity.get("enums") or {}
        if enums:
            enum_bits = [f"{key}=[{', '.join(map(str, spec.get('values', [])))}]"
                         for key, spec in enums.items()]
            line += f"; enums: {'; '.join(enum_bits)}"
        lines.append(line)
    return "\n".join(lines)


# --- drafts: author -> validate -> human approve ----------------------------------------

def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"
    return slug[:50]


def save_draft(yaml_text: str, name: str, drafts_dir: Path | str | None = None) -> Path:
    directory = Path(drafts_dir) if drafts_dir else DRAFTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slug(name)}.yaml"
    counter = 2
    while path.exists():
        path = directory / f"{_slug(name)}-{counter}.yaml"
        counter += 1
    path.write_text(yaml_text, encoding="utf-8")
    return path


def validate_draft(draft_path: Path | str, registry: Registry | None = None) -> list[str]:
    """Load-time validation exactly as the platform would: spec shape, registry
    cross-checks, and the action compiler. Returns [] when the draft is runnable."""
    registry = registry or get_registry()
    path = Path(draft_path)
    issues: list[str] = []
    try:
        load_app_spec(path, registry)          # spec shape + view fields + data sources
    except AppSpecError as exc:
        issues.append(str(exc))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        parse_app_actions(raw, path.name, registry)
    except Exception as exc:
        issues.append(str(exc))
    return issues


def approve_draft(draft_path: Path | str, approved_by: str,
                  app_specs_dir: Path | str | None = None,
                  drafts_dir: Path | str | None = None,
                  registry: Registry | None = None) -> Path:
    """Human approval step: validate, then move the draft into app_specs/."""
    drafts = Path(drafts_dir) if drafts_dir else DRAFTS_DIR
    targets = Path(app_specs_dir) if app_specs_dir else get_settings().app_specs_dir
    source = Path(draft_path)
    if not source.is_file():
        raise CompileError(f"no draft at {source}")
    issues = validate_draft(source, registry)
    if issues:
        raise CompileError("draft does not compile: " + "; ".join(issues))
    target = targets / source.name
    if target.exists():
        raise CompileError(f"{target.name} already exists in app_specs/ - approve under a new name")
    # collision check with the whole approved set
    combined = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    known = load_actions(registry, targets)
    incoming = parse_app_actions(combined, source.name, registry or get_registry())
    duplicates = set(incoming) & set(known)
    if duplicates:
        raise CompileError(f"action names already exist: {sorted(duplicates)}")
    shutil.move(str(source), str(target))
    return target


def compile_app(spec_path: Path | str, registry: Registry | None = None) -> dict:
    """Compile one app spec end to end (views + actions) - the platform's core."""
    registry = registry or get_registry()
    path = Path(spec_path)
    spec = load_app_spec(path, registry)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    actions = parse_app_actions(raw, path.name, registry)
    views = {}
    for view in spec.views:
        query, _ = compile_view(view, registry, filters={})
        views[view.name] = query.as_string(None) if hasattr(query, "as_string") else str(query)
    return {"spec": spec, "views": views, "actions": actions}
