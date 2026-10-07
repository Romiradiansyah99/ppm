"""Compiled action graphs (plan section 4): an app-spec action becomes a
LangGraph workflow - ``validate -> permission -> [approval interrupt] ->
write -> finalise``. Approval gates map to ``interrupt()`` with the Postgres
checkpointer, so a paused write survives restarts and shows up in the
approver's queue; the queue itself is the action_run ledger.

This is the single write path: apps never touch entity tables directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypedDict
from uuid import uuid4

import yaml
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import sql
from psycopg.rows import dict_row

from ppm import auth, db
from ppm.config import Settings, get_settings
from ppm.registry import Registry, get_registry
from ppm.trust import compute_weight, weight_for_author


class ActionSpecError(Exception):
    pass


GATES = {"none", "peer", "owner", "partner"}
OPERATIONS = {"insert", "update"}


@dataclass(frozen=True)
class ActionSpec:
    app: str
    name: str
    entity: str
    table: str
    operation: str
    key_field: str | None
    fields: tuple[str, ...]
    required: tuple[str, ...]
    gate: str
    on_approve: dict[str, Any] = field(default_factory=dict)
    on_reject: dict[str, Any] = field(default_factory=dict)


def parse_app_actions(spec: dict, source_name: str, registry: Registry) -> dict[str, ActionSpec]:
    """Compile one app-spec's action declarations into ActionSpecs."""
    actions: dict[str, ActionSpec] = {}
    for raw in spec.get("actions") or []:
        name = raw.get("name")
        if not name:
            raise ActionSpecError(f"{source_name}: action without a name")
        if name in actions:
            raise ActionSpecError(f"duplicate action '{name}'")
        entity = registry.resolve(raw.get("writes_to", ""))
        if entity is None:
            raise ActionSpecError(f"{name}: writes_to '{raw.get('writes_to')}' not in the registry")
        entity_fields = set(registry.entity(entity).get("fields", {}))
        fields = tuple(raw.get("fields", []))
        unknown = [f for f in fields if f not in entity_fields]
        if unknown:
            raise ActionSpecError(f"{name}: fields not in registry: {unknown}")
        operation = raw.get("operation", "insert")
        if operation not in OPERATIONS:
            raise ActionSpecError(f"{name}: operation must be one of {sorted(OPERATIONS)}")
        key_field = raw.get("key_field")
        if operation == "update" and (not key_field or key_field not in fields):
            raise ActionSpecError(f"{name}: update actions need key_field within fields")
        gate = raw.get("approval_gate", "none")
        if gate not in GATES:
            raise ActionSpecError(f"{name}: approval_gate must be one of {sorted(GATES)}")
        required = tuple(raw.get("required", []))
        missing = [r for r in required if r not in fields]
        if missing:
            raise ActionSpecError(f"{name}: required fields not in fields: {missing}")
        actions[name] = ActionSpec(
            app=str(spec.get("app", source_name)), name=name, entity=entity,
            table=registry.entity(entity)["table"],
            operation=operation, key_field=key_field, fields=fields, required=required,
            gate=gate,
            on_approve=dict(raw.get("on_approve") or {}),
            on_reject=dict(raw.get("on_reject") or {}),
        )
    return actions


def load_actions(registry: Registry | None = None, app_specs_dir=None) -> dict[str, ActionSpec]:
    """Compile every approved app-spec action declaration into an ActionSpec."""
    registry = registry or get_registry()
    directory = app_specs_dir or get_settings().app_specs_dir
    actions: dict[str, ActionSpec] = {}

    for path in sorted(Path(directory).glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        parsed = parse_app_actions(spec, path.name, registry)
        duplicates = set(parsed) & set(actions)
        if duplicates:
            raise ActionSpecError(f"duplicate action names across apps: {sorted(duplicates)}")
        actions.update(parsed)
    return actions


# --- hooks: derived columns and semantics per action --------------------------

def _lesson_submit(conn, state: dict, spec: ActionSpec) -> dict:
    actor = auth.get_person(state["actor_id"])
    return {
        "author_id": state["actor_id"],
        "layer": "private",
        "status": "draft",
        "trust_weight": weight_for_author(actor or {"person_id": state["actor_id"], "role": "consultant"}),
    }


def _lesson_promote(conn, state: dict, spec: ActionSpec) -> dict:
    from ppm.trust import author_stats_conn, log_weight_change

    decision = state.get("decision") or {}
    lesson_id = state["params"]["lesson_id"]
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM lesson_learned WHERE lesson_id = %s", (lesson_id,))
        lesson = cur.fetchone()
    if lesson is None:
        raise ValueError(f"lesson {lesson_id} not found")
    if lesson["status"] == "removed":
        raise ValueError("lesson is removed")

    old_weight = float(lesson["trust_weight"])
    approver_id = decision.get("approver_id")

    if decision.get("action") == "reject":
        new_weight = 0.0
        log_weight_change_inline(conn, lesson_id, old_weight, new_weight,
                                 f"promotion rejected: {decision.get('note') or 'no note'}", approver_id)
        return {"status": "rejected", "approver_id": approver_id, "trust_weight": new_weight}

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM person WHERE person_id = %s", (lesson["author_id"],))
        author = cur.fetchone()
    stats = author_stats_conn(conn, lesson["author_id"])
    new_weight = compute_weight(
        (author or {}).get("role", "consultant"),
        stats["accepted"] + 1,
        stats["rejected"],
        stats["last_activity"],
    )
    log_weight_change_inline(conn, lesson_id, old_weight, new_weight,
                             "promotion accepted", approver_id)
    return {"status": "shared", "layer": "shared", "approver_id": approver_id, "trust_weight": new_weight}


def _lesson_remove(conn, state: dict, spec: ActionSpec) -> dict:
    return {"status": "removed"}


def log_weight_change_inline(conn, lesson_id: str, old_weight: float | None, new_weight: float,
                             reason: str, actor_id: str | None) -> None:
    conn.execute(
        "INSERT INTO lesson_weight_log (lesson_id, old_weight, new_weight, reason, actor_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (lesson_id, old_weight, new_weight, reason, actor_id),
    )


def _deliverable_create(conn, state: dict, spec: ActionSpec) -> dict:
    return {"author_id": state["actor_id"]}


HOOKS: dict[str, Callable] = {
    "lesson.submit": _lesson_submit,
    "lesson.promote": _lesson_promote,
    "lesson.remove": _lesson_remove,
    "deliverable.create": _deliverable_create,
}


# --- the workflow --------------------------------------------------------------

class ActionState(TypedDict, total=False):
    thread_id: str
    actor_id: str
    params: dict
    decision: dict
    result: dict
    status: str
    error: str | None


def _update_run(thread_id: str, status: str, *, result: dict | None = None,
                error: str | None = None, create: bool = False,
                app: str = "", action: str = "", actor_id: str | None = None,
                params: dict | None = None) -> None:
    if create:
        db.execute(
            "INSERT INTO action_run (thread_id, app, action, actor_id, status, params) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (thread_id) DO UPDATE SET "
            "status = EXCLUDED.status, params = EXCLUDED.params, updated_at = now()",
            (thread_id, app, action, actor_id, status, json.dumps(params or {}, default=str)),
        )
        return
    db.execute(
        "UPDATE action_run SET status = %s, result = COALESCE(%s, result), error = %s, updated_at = now() "
        "WHERE thread_id = %s",
        (status, json.dumps(result, default=str) if result is not None else None, error, thread_id),
    )


class ActionWorkflow:
    def __init__(self, spec: ActionSpec, registry: Registry | None = None,
                 settings: Settings | None = None) -> None:
        self.spec = spec
        self.registry = registry or get_registry()
        self.settings = settings or get_settings()
        self._columns: set[str] | None = None
        self._app = None

    def allowed_columns(self) -> set[str]:
        """Writable columns: registry fields intersected with the live table."""
        if self._columns is None:
            rows = db.fetch_all(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (self.spec.table,),
            )
            table_columns = {row["column_name"] for row in rows}
            registry_fields = set(self.registry.entity(self.spec.entity).get("fields", {}))
            self._columns = table_columns & registry_fields
        return self._columns

    # --- nodes -------------------------------------------------------------------

    def _fail(self, state: ActionState, message: str) -> dict:
        _update_run(state["thread_id"], "failed", error=message)
        return {"status": "failed", "error": message}

    def _validate(self, state: ActionState) -> dict:
        actor = auth.get_person(state["actor_id"])
        if actor is None:
            return self._fail(state, f"unknown actor_id {state['actor_id']}")
        params = state.get("params") or {}
        unknown = [key for key in params if key not in set(self.spec.fields) | {self.spec.key_field}]
        if unknown:
            return self._fail(state, f"fields not writable by this action: {unknown}")
        for required in self.spec.required:
            value = params.get(required)
            if value in (None, ""):
                return self._fail(state, f"missing required field '{required}'")
        for field_name, value in params.items():
            if value is None:
                continue
            entity = self.registry.entity(self.spec.entity)
            enum = (entity.get("enums") or {}).get(field_name, {})
            values = enum.get("values") or []
            if values and value not in values:
                return self._fail(state, f"'{value}' is not a valid {field_name} ({values})")
        if self.spec.operation == "update":
            with db.connection() as conn:
                exists = conn.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE {} = %s").format(
                        sql.Identifier(self.spec.table), sql.Identifier(self.spec.key_field)
                    ),
                    (params.get(self.spec.key_field),),
                ).fetchone()
            if exists is None:
                return self._fail(state, f"{self.spec.key_field} {params.get(self.spec.key_field)} not found")
        return {"status": "validated"}

    def _permission(self, state: ActionState) -> dict:
        """Gate precheck: a gated submission must have at least one eligible
        approver so it can never dead-end in the queue."""
        if self.spec.gate == "none":
            return {"status": "permitted"}
        if self.spec.gate in {"owner", "partner"}:
            row = db.fetch_one("SELECT count(*) AS n FROM person WHERE is_approver AND person_id != %s",
                               (state["actor_id"],))
            if not row or row["n"] < 1:
                return self._fail(state, f"gate '{self.spec.gate}' needs an approver, none registered")
        else:  # peer
            row = db.fetch_one("SELECT count(*) AS n FROM person WHERE person_id != %s", (state["actor_id"],))
            if not row or row["n"] < 1:
                return self._fail(state, "peer gate needs at least one other person")
        return {"status": "permitted"}

    def _approval(self, state: ActionState) -> dict:
        decision = interrupt({
            "kind": "action_approval",
            "app": self.spec.app,
            "action": self.spec.name,
            "gate": self.spec.gate,
            "actor_id": state["actor_id"],
            "params": state["params"],
            "instructions": (
                "Resume with {'action': 'approve'|'reject', 'approver_id': <person uuid>, 'note': str}. "
                "owner/partner gates require an approver with is_approver."
            ),
        })
        decision = decision or {"action": "approve"}
        approver = auth.get_person(decision.get("approver_id")) if decision.get("approver_id") else None
        if approver is None:
            return self._fail(state, "approval needs a valid approver_id")
        if self.spec.gate in {"owner", "partner"} and not approver["is_approver"]:
            return self._fail(state, f"{approver['username']} is not an approver (gate '{self.spec.gate}')")
        if (self.spec.gate == "peer" and not approver["is_approver"]
                and str(approver["person_id"]) == str(state["actor_id"])):
            return self._fail(state, "peer approval must come from someone other than the author")
        _update_run(state["thread_id"], "running")
        return {"decision": decision, "status": "decided"}

    def _write(self, state: ActionState) -> dict:
        spec = self.spec
        params = state.get("params") or {}
        values: dict[str, Any] = {key: value for key, value in params.items() if key in spec.fields}
        if spec.gate != "none":
            decision = state.get("decision") or {}
            values.update(spec.on_approve if decision.get("action") != "reject" else spec.on_reject)

        try:
            with db.connection() as conn:
                hook = HOOKS.get(spec.name)
                if hook is not None:
                    try:
                        values.update(hook(conn, state, spec))
                    except ValueError as exc:
                        return self._fail(state, str(exc))
                allowed = self.allowed_columns()
                rejected = [key for key in values if key not in allowed]
                if rejected:
                    return self._fail(state, f"columns not writable: {rejected}")
                if not values:
                    return self._fail(state, "nothing to write")

                columns = list(values)
                with conn.cursor(row_factory=dict_row) as cur:
                    if spec.operation == "insert":
                        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                            sql.Identifier(spec.table),
                            sql.SQL(", ").join(map(sql.Identifier, columns)),
                            sql.SQL(", ").join(sql.Placeholder() * len(columns)),
                        )
                        row = cur.execute(statement, [values[c] for c in columns]).fetchone()
                    else:
                        row = cur.execute(
                            sql.SQL("UPDATE {} SET {} WHERE {} = %s RETURNING *").format(
                                sql.Identifier(spec.table),
                                sql.SQL(", ").join(
                                    sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in columns
                                ),
                                sql.Identifier(spec.key_field),
                            ),
                            [*[values[c] for c in columns], params[spec.key_field]],
                        ).fetchone()
                        if row is None:
                            return self._fail(state, f"{spec.key_field} not found at write time")
                record = _clean_record(row)
        except Exception as exc:
            return self._fail(state, f"write failed: {exc}")
        return {"result": record, "status": "written"}

    def _finalise(self, state: ActionState) -> dict:
        decision = state.get("decision") or {}
        final = "rejected" if (self.spec.gate != "none" and decision.get("action") == "reject") else "completed"
        _update_run(state["thread_id"], final, result=state.get("result") or {})
        return {"status": final}

    # --- routing -------------------------------------------------------------------

    def _after_validate(self, state: ActionState) -> str:
        return "go" if state.get("status") == "validated" else "stop"

    def _after_permission(self, state: ActionState) -> str:
        if state.get("status") != "permitted":
            return "stop"
        return "approval" if self.spec.gate != "none" else "write"

    def _after_approval(self, state: ActionState) -> str:
        return "go" if state.get("status") == "decided" else "stop"

    def _after_write(self, state: ActionState) -> str:
        return "finalise" if state.get("status") == "written" else "stop"

    # --- assembly --------------------------------------------------------------------

    def compile(self, checkpointer=None):
        graph = StateGraph(ActionState)
        graph.add_node("validate", self._validate)
        graph.add_node("permission", self._permission)
        graph.add_node("approval", self._approval)
        graph.add_node("write", self._write)
        graph.add_node("finalise", self._finalise)
        graph.add_edge(START, "validate")
        graph.add_conditional_edges("validate", self._after_validate, {"go": "permission", "stop": END})
        graph.add_conditional_edges("permission", self._after_permission,
                                    {"approval": "approval", "write": "write", "stop": END})
        graph.add_conditional_edges("approval", self._after_approval, {"go": "write", "stop": END})
        graph.add_conditional_edges("write", self._after_write, {"finalise": "finalise", "stop": END})
        graph.add_edge("finalise", END)
        self._app = graph.compile(checkpointer=checkpointer)
        return self._app

    # --- execution ---------------------------------------------------------------------

    def run(self, actor_id: str, params: dict, thread_id: str | None = None) -> dict:
        thread_id = thread_id or str(uuid4())
        _update_run(thread_id, "running", create=True, app=self.spec.app, action=self.spec.name,
                    actor_id=actor_id, params=params)
        state: ActionState = {"thread_id": thread_id, "actor_id": actor_id, "params": params}
        return self._app.invoke(state, config={"configurable": {"thread_id": thread_id}})

    def resume(self, thread_id: str, decision: dict) -> dict:
        return self._app.invoke(Command(resume=decision), config={"configurable": {"thread_id": thread_id}})


def _clean_record(row: dict | None) -> dict:
    from decimal import Decimal

    if not row:
        return {}
    record = {}
    for name, value in row.items():
        if name == "password_hash":
            record[name] = "***"
        elif isinstance(value, Decimal):
            record[name] = float(value)
        elif hasattr(value, "isoformat"):
            record[name] = value.isoformat()
        elif value.__class__.__name__ == "UUID":
            record[name] = str(value)
        else:
            record[name] = value
    return record
