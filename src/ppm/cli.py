"""ppm CLI.

Commands: init-db, ingest, review (list/show/approve/reject), search, status,
registry check, models check. Friendly errors only - never a stack trace to
the user (plan section 4, failure mode rule).
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone

from ppm import __version__, db
from ppm.appspec import AppSpecError, load_app_spec
from ppm.config import ConfigError, get_settings
from ppm.migrate import migrate
from ppm.models import ModelUnavailable, check_models
from ppm.registry import RegistryError, get_registry
from ppm.workflows.ingest import IngestWorkflow, interrupted
from ppm.workflows.retrieval import RateQuery, rate_search

DEPRECATION_DAYS = 90


@contextmanager
def _checkpointer(settings):
    """Postgres-backed checkpointer so a paused review survives restarts."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.dsn) as saver:
        saver.setup()
        yield saver


def _idr(value: float | int | None) -> str:
    if value is None:
        return "-"
    return "Rp " + f"{value:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_init_db(args: argparse.Namespace) -> int:
    settings = get_settings()
    applied = migrate()
    if applied:
        print("applied migrations: " + ", ".join(applied))
    else:
        print("schema already current")
    with _checkpointer(settings):
        pass
    tables = db.fetch_all(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    )
    print(f"tables in place ({len(tables)}): " + ", ".join(t["table_name"] for t in tables))
    print("database ready")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    workflow = IngestWorkflow(settings)
    failures = 0
    with _checkpointer(settings) as checkpointer:
        workflow.compile(checkpointer)
        for raw_path in args.paths:
            result = workflow.run(raw_path)
            thread = result.get("thread_id", "")
            print(f"{raw_path}: {result.get('status')}")
            if result.get("error"):
                print(f"  error: {result['error']}")
                failures += 1
            if interrupted(result) and args.auto_approve:
                print("  --auto-approve: approving review queue (demo only)")
                while interrupted(result):
                    result = workflow.resume(thread, {"action": "approve"})
                print(f"  final status: {result.get('status')}")
            elif interrupted(result):
                db.execute("UPDATE ingest_run SET status = 'awaiting_review', updated_at = now() WHERE thread_id = %s",
                           (thread,))
                print(f"  awaiting human review - thread {thread}")
                print(f"  inspect: ppm review show {thread}")
                print(f"  approve: ppm review approve {thread}")
                print(f"  reject:  ppm review reject {thread}")
                continue
            stats = result.get("stats") or {}
            if result.get("status") == "completed":
                print(f"  project: {stats.get('project_id')}  cost plan: {stats.get('costplan_id')}")
                print(f"  rates written: {stats.get('rates_written')}  rows dropped: {stats.get('rows_dropped')}")
                if stats.get("embedding_warning"):
                    print(f"  warning: {stats['embedding_warning']}")
            elif result.get("status") == "duplicate":
                print("  identical file already indexed - nothing written")
    return 1 if failures else 0


def cmd_review(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.review_cmd == "list":
        rows = db.fetch_all(
            "SELECT thread_id, source_path, created_at FROM ingest_run "
            "WHERE status = 'awaiting_review' ORDER BY created_at"
        )
        if not rows:
            print("no documents awaiting review")
            return 0
        for row in rows:
            print(f"{row['thread_id']}  {row['source_path']}  ({row['created_at']:%Y-%m-%d %H:%M})")
        return 0

    workflow = IngestWorkflow(settings)
    thread = args.thread

    if args.review_cmd == "show":
        with _checkpointer(settings) as checkpointer:
            app = workflow.compile(checkpointer)
            snapshot = app.get_state({"configurable": {"thread_id": thread}})
            payload = None
            for task in getattr(snapshot, "tasks", ()):
                for intr in getattr(task, "interrupts", ()):
                    payload = intr.value
                    break
            if payload is None and not snapshot.values:
                print(f"no checkpoint for thread {thread}")
                return 1
            items = (payload or {}).get("items") or (snapshot.values.get("validation") or {}).get("review_items", [])
            print(f"thread {thread}")
            if payload:
                print(f"document: {payload.get('source_path')}")
            if not items:
                print("no pending review items")
                return 0
            for item in items:
                where = item.get("source_ref") or "document"
                confidence = item.get("confidence")
                conf = f" conf={confidence:.2f}" if isinstance(confidence, (int, float)) else ""
                print(f"  [{item.get('severity')}] row={item.get('row_index')} {where}{conf}")
                if item.get("description"):
                    print(f"      {item['description'][:100]}")
                print(f"      {item.get('problem')}")
        return 0

    if args.review_cmd in {"approve", "reject"}:
        decisions = {
            "action": args.review_cmd,
            "note": getattr(args, "note", None),
        }
        if args.review_cmd == "approve" and getattr(args, "drop", None):
            decisions["drop_rows"] = [int(i) for i in args.drop.split(",") if i.strip()]
        with _checkpointer(settings) as checkpointer:
            workflow.compile(checkpointer)
            result = workflow.resume(thread, decisions)
        print(f"thread {thread}: {result.get('status')}")
        stats = result.get("stats") or {}
        if result.get("error"):
            print(f"  error: {result['error']}")
            return 1
        if result.get("status") == "completed":
            print(f"  rates written: {stats.get('rates_written')}  rows dropped: {stats.get('rows_dropped')}")
            if stats.get("embedding_warning"):
                print(f"  warning: {stats['embedding_warning']}")
        return 0
    return 1


def cmd_search(args: argparse.Namespace) -> int:
    settings = get_settings()
    query = RateQuery(
        text=args.text,
        sector=args.sector,
        spec_level=args.spec_level,
        element_code=args.element_code,
        date_from=date.fromisoformat(args.date_from) if args.date_from else None,
        date_to=date.fromisoformat(args.date_to) if args.date_to else None,
        limit=args.limit,
    )
    rows, degraded = rate_search(query, settings)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if degraded:
        print(f"note: {degraded}")
    if not rows:
        print("no matching rates")
        return 0
    for row in rows:
        flag = "  [UNCLASSIFIED]" if row["unclassified"] else ""
        similarity = f"  sim={row['similarity']:.3f}" if row.get("similarity") is not None else ""
        print(f"{_idr(row['rate_idr']):>18} /{row['unit']:<5} {row['element_code']:<8}"
              f"{row['date_normalised_to']}  {row['description'][:70]}{similarity}{flag}")
        print(f"{'':>19} source: {row['project_name']} v{row['costplan_version']}  {row['source_ref']}"
              f"  ({row['document_hash'][:12]}...)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    registry = get_registry(str(settings.registry_dir))

    documents = db.fetch_all("SELECT ingest_status, count(*) AS n FROM document GROUP BY ingest_status ORDER BY ingest_status")
    totals = db.fetch_one(
        "SELECT (SELECT count(*) FROM project) AS projects, (SELECT count(*) FROM cost_plan) AS cost_plans, "
        "(SELECT count(*) FROM benchmark_rate) AS rates, "
        "(SELECT count(*) FROM benchmark_rate WHERE embedding IS NOT NULL) AS embedded"
    )
    print(f"registry {registry.version} at {settings.registry_dir}")
    print(f"provider {settings.provider} (residency {settings.residency})")
    print(f"projects {totals['projects']}  cost plans {totals['cost_plans']}  rates {totals['rates']}"
          f"  with embeddings {totals['embedded']}")
    for row in documents:
        print(f"documents {row['ingest_status']:<16} {row['n']}")

    runs = db.fetch_all(
        "SELECT thread_id, source_path, status, created_at FROM ingest_run ORDER BY created_at DESC LIMIT 5"
    )
    for row in runs:
        print(f"ingest {row['created_at']:%Y-%m-%d %H:%M}  {row['status']:<16} {row['source_path']}")
    pending = db.fetch_one("SELECT count(*) AS n FROM ingest_run WHERE status = 'awaiting_review'")
    if pending["n"]:
        print(f"AWAITING REVIEW: {pending['n']} document(s) - ppm review list")

    last = db.fetch_one("SELECT created_at FROM search_log ORDER BY created_at DESC LIMIT 1")
    if last:
        days = (datetime.now(timezone.utc) - last["created_at"]).days
        print(f"last search {days} day(s) ago")
        if days >= DEPRECATION_DAYS:
            print(f"DEPRECATION CONDITION MET ({DEPRECATION_DAYS} days without a search, app spec)")
    else:
        print("no searches recorded yet")

    unresolved = registry.unresolved_definitions()
    print(f"unresolved definitions: {len(unresolved)}")
    for line in unresolved[:10]:
        print(f"  {line}")
    if len(unresolved) > 10:
        print(f"  ... {len(unresolved) - 10} more")

    try:
        spec = load_app_spec(settings.app_specs_dir / "rate-lookup.yaml", registry)
        if spec.user in ("", "UNASSIGNED"):
            print("app rate-lookup: no named user yet (plan section 3.5: no name, no start)")
    except AppSpecError as exc:
        print(f"app spec: {exc}")
    return 0


def cmd_registry_check(args: argparse.Namespace) -> int:
    registry = get_registry()
    print(f"registry {registry.version} loaded: {', '.join(sorted(registry.entities))}")
    unresolved = registry.unresolved_definitions()
    print(f"unresolved definitions: {len(unresolved)}")
    for line in unresolved:
        print(f"  {line}")
    return 0


def cmd_models_check(args: argparse.Namespace) -> int:
    report = check_models(get_settings())
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppm", description="PPM Phase 0 - office truth layer and rate lookup")
    parser.add_argument("--version", action="version", version=f"ppm {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="apply migrations and set up the checkpointer").set_defaults(func=cmd_init_db)

    ingest = sub.add_parser("ingest", help="run the ingest graph over one or more files")
    ingest.add_argument("paths", nargs="+")
    ingest.add_argument("--auto-approve", action="store_true",
                        help="auto-approve the review queue (demo only, skips the human gate)")
    ingest.set_defaults(func=cmd_ingest)

    review = sub.add_parser("review", help="human review queue for paused ingests")
    review_sub = review.add_subparsers(dest="review_cmd", required=True)
    review_sub.add_parser("list")
    show = review_sub.add_parser("show")
    show.add_argument("thread")
    approve = review_sub.add_parser("approve")
    approve.add_argument("thread")
    approve.add_argument("--drop", help="comma-separated element indexes to drop before writing")
    approve.add_argument("--note")
    reject = review_sub.add_parser("reject")
    reject.add_argument("thread")
    reject.add_argument("--note")
    review.set_defaults(func=cmd_review)

    search = sub.add_parser("search", help="hybrid rate search")
    search.add_argument("text", nargs="?", default=None)
    search.add_argument("--sector")
    search.add_argument("--spec-level")
    search.add_argument("--element-code")
    search.add_argument("--from", dest="date_from")
    search.add_argument("--to", dest="date_to")
    search.add_argument("--limit", type=int, default=25)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    sub.add_parser("status", help="truth-layer counters, review queue, deprecation check").set_defaults(func=cmd_status)

    registry = sub.add_parser("registry", help="definition registry utilities")
    registry_sub = registry.add_subparsers(dest="registry_cmd", required=True)
    registry_sub.add_parser("check")
    registry.set_defaults(func=cmd_registry_check)

    models = sub.add_parser("models", help="model endpoint diagnostics")
    models_sub = models.add_subparsers(dest="models_cmd", required=True)
    models_sub.add_parser("check")
    models.set_defaults(func=cmd_models_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, RegistryError, AppSpecError, ModelUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # last resort: friendly line, no stack trace
        if exc.__class__.__module__.startswith("psycopg"):
            print(f"error: database problem: {exc}", file=sys.stderr)
            print("hint: is postgres running? scripts/dev_pg.ps1 start", file=sys.stderr)
            return 1
        name = type(exc).__name__
        print(f"error: {name}: {exc}", file=sys.stderr)
        if name == "PostgresSaverNotFound" or "checkpoint" in str(exc).lower():
            print("hint: run ppm init-db first", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
