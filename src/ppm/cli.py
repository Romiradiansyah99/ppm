"""PPM v2 command line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from ppm import db
from ppm.config import REPO_ROOT, Settings, get_settings
from ppm.corpus.manifest import resolve_corpus
from ppm.corpus.report import build_report, format_report
from ppm.eval import evaluate, write_report
from ppm.migrate import migrate
from ppm.models import check_models
from ppm.registry import load_taxonomy, validate_app_specs, validate_taxonomy
from ppm.workflows.retrieval import TemplateQuery, find_templates

CATALOG_FILES = [
    ("idpm", "IDPM Templates/IDPM - List of Templates.xlsx"),
    ("ttpm_flat", "00. Template T&T/PM/PM/0 - List of Templates.xlsx"),
    ("cm", "00. Template T&T/CM/CM/Brilliant basics template/CM Templates List 2017.xlsx"),
    ("idcm", "00. Template T&T/OneDrive_2026-08-22 (1)/IDCM Templates/IDCM List of Templates.xlsx"),
]


def _settings() -> Settings:
    return get_settings()


def _print(value: object) -> None:
    print(value)


# --- commands -----------------------------------------------------------------


def cmd_agent(args: argparse.Namespace) -> int:
    from ppm.analyst.memory import memory_context
    from ppm.auth import authenticate
    from ppm.conversations import add_message, create_conversation, get_conversation, get_last_conversation, set_hermes_session
    from ppm.hermes import HermesClient, build_system_prompt, extract_results
    from ppm.tool_context import issue

    settings = _settings()
    password = args.password or os.environ.get("PPM_CLI_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {args.user}: ")
    user = authenticate(args.user, password)
    if not user:
        print("authentication failed")
        return 1
    if not settings.tool_secret:
        print("PPM_TOOL_SECRET is required for agent tool calls")
        return 2
    base, key = settings.hermes_for(user["role"])
    if not base or not key:
        print("Hermes is not configured; set PPM_HERMES_* in .env")
        return 2

    conversation = (
        get_conversation(user["user_id"], thread_id=args.thread)
        if args.thread
        else get_last_conversation(user["user_id"])
    ) or create_conversation(user["user_id"])
    set_hermes_session(conversation["conversation_id"], conversation.get("hermes_session_id"))
    add_message(conversation["conversation_id"], "user", args.question)
    client = HermesClient(base, key, model=settings.hermes_model, timeout=settings.hermes_timeout)
    session_id = conversation.get("hermes_session_id")
    if not session_id:
        session_id = client.create_session(title=conversation["title"])
        set_hermes_session(conversation["conversation_id"], session_id)
    token = issue(user, conversation["conversation_id"], settings.tool_secret)
    system_prompt = build_system_prompt(
        user=user,
        memory=memory_context(user["user_id"], args.question),
        summary=conversation.get("summary"),
        context_token=token,
    )
    final, events = client.run_turn(
        session_id=session_id,
        system_prompt=system_prompt,
        user_message=args.question,
    )
    answer = final.get("answer") or ""
    try:
        extracted = extract_results(client.fetch_messages(session_id))
        answer = answer or extracted.get("answer") or ""
        final["artifacts"] = extracted.get("artifacts") or final.get("artifacts") or []
        final["documents"] = extracted.get("documents") or final.get("documents") or []
    except Exception:
        pass
    add_message(conversation["conversation_id"], "assistant", answer, events=events)
    print(json.dumps({
        "thread_id": conversation["thread_id"],
        "answer": answer,
        "artifacts": final.get("artifacts") or [],
        "documents": final.get("documents") or [],
        "events": events,
    }, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    applied = migrate()
    if applied:
        print("applied: " + ", ".join(applied))
    else:
        print("schema up to date")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    rows = db.fetch_all(
        "SELECT ingest_status, count(*) AS count FROM document GROUP BY ingest_status ORDER BY ingest_status"
    )
    print("documents:")
    for row in rows:
        print(f"  {row['ingest_status']:<18} {row['count']}")
    if not rows:
        print("  (none)")
    chunk_count = db.fetch_one("SELECT count(*) AS count FROM document_chunk")["count"]
    template_count = db.fetch_one("SELECT count(*) AS count FROM template")["count"]
    run_rows = db.fetch_all(
        "SELECT status, count(*) AS count FROM ingest_run GROUP BY status ORDER BY status"
    )
    print(f"chunks:    {chunk_count}")
    print(f"templates: {template_count}")
    print("ingest runs:")
    for row in run_rows:
        print(f"  {row['status']:<18} {row['count']}")
    return 0


def cmd_models_check(_: argparse.Namespace) -> int:
    report = check_models(_settings())
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


def _print_run(result) -> None:
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "report_path": result.report_path,
        "error": result.error,
        "artifacts": result.artifacts,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def cmd_analyst_demo(_: argparse.Namespace) -> int:
    from ppm.analyst.demo import demo_codegen, demo_plan
    from ppm.analyst.executor import run_plan

    settings = _settings()
    result = run_plan(
        demo_plan(),
        user_label=settings.user_label,
        codegen=demo_codegen(),
        approved=True,
        model="static-demo",
        access_level="all",
    )
    _print_run(result)
    return 0 if result.status == "completed" else 1


def cmd_analyst_run(args: argparse.Namespace) -> int:
    from ppm.analyst.codegen import codegen_for
    from ppm.analyst.executor import run_plan
    from ppm.analyst.schemas import AnalysisPlan

    settings = _settings()
    plan_path = Path(args.plan)
    plan = AnalysisPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    if not args.auto:
        if not sys.stdin.isatty():
            print("non-interactive shell: pass --auto to run the plan")
            return 2
        answer = input("Run this plan? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("rejected")
            return 1
    codegen = codegen_for(settings, args.codegen)
    result = run_plan(
        plan,
        user_label=settings.user_label,
        codegen=codegen,
        approved=True,
        model=settings.llm_model or args.codegen,
        access_level=args.access_level,
    )
    _print_run(result)
    return 0 if result.status == "completed" else 1


def cmd_analyst_plan(args: argparse.Namespace) -> int:
    from ppm.analyst.planner import plan_question

    settings = _settings()
    plan = plan_question(args.question, settings)
    payload = plan.model_dump_json(indent=2, by_alias=True)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"plan written: {args.out}")
    else:
        print(payload)
    return 0


def cmd_analyst_ask(args: argparse.Namespace) -> int:
    from ppm.analyst.codegen import codegen_for
    from ppm.analyst.executor import run_plan
    from ppm.analyst.planner import plan_question

    settings = _settings()
    plan = plan_question(args.question, settings, access_level=args.access_level)
    if not args.auto:
        if not sys.stdin.isatty():
            print("non-interactive shell: pass --auto to run the plan")
            return 2
        print(plan.model_dump_json(indent=2, by_alias=True))
        answer = input("Run this plan? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("rejected")
            return 1
    result = run_plan(
        plan,
        user_label=settings.user_label,
        codegen=codegen_for(settings, args.codegen),
        approved=True,
        model=settings.llm_model,
        access_level=args.access_level,
    )
    _print_run(result)
    return 0 if result.status == "completed" else 1


def cmd_analyst_status(args: argparse.Namespace) -> int:
    from ppm.analyst.artifacts import list_artifacts

    run = db.fetch_one("SELECT * FROM analysis_run WHERE run_id = %s", (args.run_id,))
    if not run:
        print("run not found")
        return 1
    tasks = db.fetch_all(
        "SELECT task_key, name, kind, status, duration_ms, output_path, validation, error "
        "FROM analysis_task WHERE run_id = %s ORDER BY started_at NULLS LAST, task_key",
        (args.run_id,),
    )
    artifacts = list_artifacts(args.run_id)
    payload = {
        "run": {key: str(value) for key, value in run.items()},
        "tasks": [{key: str(value) for key, value in row.items()} for row in tasks],
        "artifacts": [{key: str(value) for key, value in row.items()} for row in artifacts],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"run {run['run_id']}  status={run['status']}  model={run['model']}")
    print(f"question: {run['question']}")
    if run["error"]:
        print(f"error: {run['error']}")
    for task in tasks:
        print(f"  {task['status']:<10} {task['task_key']:<18} {task['duration_ms'] or 0:>6} ms  {task['output_path'] or ''}")
    for artifact in artifacts:
        print(f"  artifact {artifact['kind']:<10} {artifact['name']:<28} {artifact['path']}")
    return 0


def cmd_analyst_eval(args: argparse.Namespace) -> int:
    from ppm.analyst.eval import evaluate, write_report

    report = evaluate(args.benchmarks, _settings(), args.codegen)
    print(json.dumps(report["summary"], indent=2))
    for item in report["items"]:
        if not item.get("passed"):
            print(f"FAIL  {item.get('id')}: {item.get('problems')}")
    if args.out:
        path = write_report(report, args.out)
        print(f"report: {path}")
    return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


def cmd_analyst_teach(args: argparse.Namespace) -> int:
    from ppm.analyst.teach import propose_lesson, record_teaching

    event_id = record_teaching(args.run_id, args.note)
    lesson = propose_lesson(event_id, _settings())
    print(json.dumps({
        "event_id": event_id,
        "run_id": args.run_id,
        "proposed_lesson": lesson,
        "status": "proposed",
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_analyst_benchmarks(args: argparse.Namespace) -> int:
    from ppm.analyst.teach import list_benchmarks

    rows = list_benchmarks()
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print("no benchmark cases yet")
        return 0
    for row in rows:
        print(f"{row['case_id']}  {row['status']:<10} {row['question']}")
        print(f"    checks: {row['expected_checks']}")
    return 0


def cmd_user_seed(_: argparse.Namespace) -> int:
    from ppm.auth import ensure_seed_users, list_users

    created = ensure_seed_users()
    if created:
        for user in created:
            print(f"created: {user['username']} ({user['role']})")
    else:
        print("users already exist")
    for user in list_users():
        print(f"  {user['username']:<16} {user['role']:<16} {user['display_name']}")
    return 0


def cmd_user_list(_: argparse.Namespace) -> int:
    from ppm.auth import list_users

    for user in list_users():
        print(f"{user['username']:<16} {user['role']:<16} {user['display_name']}")
    return 0


def cmd_user_passwd(args: argparse.Namespace) -> int:
    from ppm.auth import set_password

    if not set_password(args.username, args.password):
        print("user not found")
        return 1
    print(f"password updated: {args.username}")
    return 0


def cmd_user_invite(args: argparse.Namespace) -> int:
    from ppm.auth import create_invite

    code = create_invite(
        args.role,
        email=args.email,
        expires_hours=args.expires_hours,
    )
    print(f"invite for {args.email or '(no email)'} ({args.role}), expires in {args.expires_hours}h:")
    print(code)
    return 0


def cmd_user_bootstrap(args: argparse.Namespace) -> int:
    from ppm.auth import bootstrap_admin

    password = args.password or os.environ.get("PPM_BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("New admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("passwords do not match")
            return 1
    try:
        user = bootstrap_admin(args.username, args.display_name, password)
    except Exception as exc:
        print(str(exc))
        return 1
    print(f"created admin: {user['username']}")
    return 0


def cmd_user_revoke(args: argparse.Namespace) -> int:
    from ppm.auth import revoke_user_sessions

    user_id = _user_id(args.username)
    if not user_id:
        print("user not found")
        return 1
    count = revoke_user_sessions(user_id)
    print(f"revoked {count} session(s) for {args.username}")
    return 0


def _user_id(username: str) -> str | None:
    row = db.fetch_one(
        "SELECT user_id FROM app_user WHERE lower(username) = lower(%s)", (username,)
    )
    return str(row["user_id"]) if row else None


def cmd_memory_list(args: argparse.Namespace) -> int:
    from ppm.analyst.memory import list_memories

    user_id = _user_id(args.user)
    if not user_id:
        print("user not found")
        return 1
    rows = list_memories(user_id, limit=args.limit)
    if not rows:
        print("no memory")
        return 0
    for row in rows:
        print(f"{row['created_at']}  [{row['kind']}]  {row['content']}")
    return 0


def cmd_memory_clear(args: argparse.Namespace) -> int:
    from ppm.analyst.memory import clear

    user_id = _user_id(args.user)
    if not user_id:
        print("user not found")
        return 1
    print(f"deleted: {clear(user_id)}")
    return 0


def cmd_memory_prune(args: argparse.Namespace) -> int:
    from ppm.analyst.memory import prune

    print(f"deleted: {prune(args.days)}")
    return 0


def cmd_data_seed(args: argparse.Namespace) -> int:
    from ppm.data.seed import seed_defaults

    stats = seed_defaults(dry_run=args.dry_run)
    print(json.dumps({
        "dry_run": args.dry_run,
        "projects": stats.projects,
        "cost_plans": stats.cost_plans,
        "cost_lines": stats.cost_lines,
        "benchmark_rates": stats.benchmark_rates,
        "risks": stats.risks,
        "changes": stats.changes,
        "skipped": stats.skipped,
        "warnings": stats.warnings,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_data_report(args: argparse.Namespace) -> int:
    from ppm.data.report import build_report, format_report

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_report(report))
    return 0


def cmd_corpus_report(args: argparse.Namespace) -> int:
    settings = _settings()
    report, duplicates = build_report(args.manifest or settings.manifest_path, compute_hash=not args.no_hash)
    if args.json:
        print(json.dumps({
            "base_dir": str(report.base_dir),
            "entries": len(report.entries),
            "indexable": len(report.indexable),
            "catalog_only": len(report.catalog_only),
            "by_collection": report.by_collection(),
            "by_licence": report.by_licence(),
            "duplicates": [
                {"hash": group.hash, "paths": group.paths} for group in duplicates
            ],
            "excluded": [
                {"path": item.path, "reason": item.reason} for item in report.excluded
            ],
            "warnings": report.warnings,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, duplicates))
    return 0


def cmd_corpus_redact(_: argparse.Namespace) -> int:
    from ppm.corpus.redact import load_blocklist, redact_text

    settings = _settings()
    terms = load_blocklist(settings.manifest_path.parent / "client_blocklist.txt")
    rows = db.fetch_all("SELECT template_id, title FROM template")
    changed = 0
    for row in rows:
        cleaned, hits = redact_text(row["title"], terms)
        if hits and cleaned != row["title"]:
            db.execute("UPDATE template SET title = %s WHERE template_id = %s", (cleaned, row["template_id"]))
            changed += 1
            print(f"redacted: {row['title']} -> {cleaned}")
    print(f"templates checked: {len(rows)}; redacted: {changed}")
    return 0


def cmd_corpus_catalog(args: argparse.Namespace) -> int:
    from ppm.corpus.catalog import read_catalog_rows
    from ppm.corpus.redact import load_blocklist, redact_text

    settings = _settings()
    base_dir = resolve_corpus(settings.manifest_path).base_dir
    taxonomy = load_taxonomy(settings.taxonomy_path)
    terms = load_blocklist(settings.manifest_path.parent / "client_blocklist.txt")
    total = 0
    updated = 0

    for collection, relative in CATALOG_FILES:
        path = base_dir / relative
        if not path.is_file():
            print(f"missing catalog: {path}")
            continue
        rows = [row for row in read_catalog_rows(path) if row.get("title")]
        print(f"{collection}: {len(rows)} rows from {path.name}")
        total += len(rows)
        if args.dry_run:
            for row in rows[:5]:
                print(f"  {row.get('code') or '-':>5}  {row.get('title')}")
            continue
        for row in rows:
            if not row.get("code"):
                continue
            stage = row.get("stage") if row.get("stage") in taxonomy.stages else None
            category = row.get("category") if row.get("category") in taxonomy.categories else None
            title, _ = redact_text(row["title"], terms)
            changed = db.execute(
                "UPDATE template SET title = %s, stage = COALESCE(%s, stage), "
                "category = COALESCE(%s, category) "
                "WHERE source_collection = %s AND code = %s AND variant_of IS NULL",
                (title, stage, category, collection, row["code"]),
            )
            updated += changed
    print(f"catalog rows: {total}; templates updated: {updated}")
    return 0


def _select_entries(args, settings: Settings):
    report = resolve_corpus(args.manifest or settings.manifest_path)
    entries = report.entries if args.include_reference else report.indexable
    if args.paths:
        wanted = {str(Path(item).resolve()).lower() for item in args.paths}
        names = {Path(item).name.lower() for item in args.paths}
        entries = [
            entry for entry in entries
            if str(entry.path).lower() in wanted or entry.path.name.lower() in names
        ]
    else:
        known = {
            row["source_path"].lower()
            for row in db.fetch_all("SELECT source_path FROM document")
        } | {
            row["source_path"].lower()
            for row in db.fetch_all("SELECT source_path FROM document_alias")
        }
        entries = [entry for entry in entries if str(entry.path).lower() not in known]
    if args.limit:
        entries = entries[: args.limit]
    return entries


def cmd_ingest(args: argparse.Namespace) -> int:
    from ppm.workflows.ingest import IngestWorkflow

    settings = _settings()
    entries = _select_entries(args, settings)
    if not entries:
        print("nothing to ingest")
        return 0

    print(f"ingesting {len(entries)} file(s)")
    failures = 0
    workflow = IngestWorkflow(settings)
    for entry in entries:
        try:
            result = workflow.run(entry)
        except Exception as exc:
            failures += 1
            print(f"FAILED  {entry.relative_path}: {exc}")
            continue
        status = result.get("status", "unknown")
        thread_id = result.get("thread_id", "")
        if status == "awaiting_review":
            print(f"REVIEW  {entry.relative_path}  thread={thread_id}")
        elif status == "failed":
            failures += 1
            print(f"FAILED  {entry.relative_path}: {result.get('error')}")
        else:
            print(f"{status.upper():<12} {entry.relative_path}")
    return 1 if failures else 0


def cmd_review(args: argparse.Namespace) -> int:
    from ppm.workflows.ingest import IngestWorkflow

    settings = _settings()
    if args.review_action == "list":
        rows = db.fetch_all(
            "SELECT thread_id, source_path, status, stats FROM ingest_run "
            "WHERE status = 'awaiting_review' ORDER BY created_at"
        )
        if not rows:
            print("review queue is empty")
            return 0
        for row in rows:
            payload = (row["stats"] or {}).get("review") or {}
            reasons = ", ".join(payload.get("reasons") or [])
            print(f"{row['thread_id']}  {row['source_path']}")
            print(f"    reasons: {reasons or '-'}")
        return 0

    if not args.thread:
        print("thread id is required")
        return 2

    if args.review_action == "show":
        row = db.fetch_one("SELECT * FROM ingest_run WHERE thread_id = %s", (args.thread,))
        if not row:
            print("no such thread")
            return 1
        payload = (row["stats"] or {}).get("review") or {}
        print(json.dumps({
            "thread_id": row["thread_id"],
            "source_path": row["source_path"],
            "status": row["status"],
            "review": payload,
            "stats": row["stats"],
        }, indent=2, ensure_ascii=False))
        return 0

    decision: dict = {}
    if args.review_action == "approve":
        decision = {"action": "approve_redacted"}
        if args.licence:
            decision["licence_class"] = args.licence
        if args.confidentiality:
            decision["confidentiality_class"] = args.confidentiality
    elif args.review_action == "reject":
        decision = {"action": "forbid" if args.forbid else "reference_only"}

    workflow = IngestWorkflow(settings)
    result = workflow.resume(args.thread, decision)
    print(json.dumps({
        "thread_id": args.thread,
        "status": result.get("status"),
        "error": result.get("error"),
        "stats": result.get("stats"),
    }, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("status") not in {"failed"} else 1


def cmd_find(args: argparse.Namespace) -> int:
    rows = find_templates(TemplateQuery(
        text=args.text,
        stage=args.stage,
        category=args.category,
        format=args.format,
        collection=args.collection,
        limit=args.limit,
    ))
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print("No templates found.")
        return 1
    for row in rows:
        code = row.get("code") or "-"
        blank = "blank" if row.get("is_blank") else "sample"
        print(f"{code:>5}  {row['title']}")
        print(f"       {row['stage']} / {row['category'] or 'other'} / {row['format']} / {blank}")
        print(f"       {row.get('source_path') or '(catalog only)'}")
    print(f"\n{len(rows)} template(s)")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from ppm.workflows.rag import AskWorkflow

    workflow = AskWorkflow(_settings())
    result = workflow.ask(
        args.question,
        collection=args.collection,
        stage=args.stage,
        mode="literal" if args.literal else "auto",
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    print(result.get("answer") or "No source found.")
    chunks = result.get("chunks") or []
    if chunks:
        print("\nSources:")
        for index, chunk in enumerate(chunks, start=1):
            print(f"  [S{index}] {chunk.get('source_path')} -> {chunk.get('section_ref')}")
    stats = result.get("stats") or {}
    if result.get("degraded"):
        print(f"\n(degraded: {result['degraded']})")
    if stats.get("refused"):
        print(f"(refused: {stats.get('reason')})")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    from ppm.corpus.redact import load_blocklist
    from ppm.loaders import ParsedDocument, document_stats, load_document
    from ppm.workflows.chunking import index_document_chunks

    settings = _settings()
    documents = db.fetch_all(
        "SELECT document_id, source_path FROM document "
        "WHERE ingest_status IN ('indexed', 'indexed_text_only') ORDER BY created_at"
    )
    terms = load_blocklist(settings.manifest_path.parent / "client_blocklist.txt")
    exit_code = 0
    for document in documents:
        path = Path(document["source_path"])
        if not path.is_file():
            print(f"SKIP    {path} (file missing)")
            continue
        try:
            parsed = load_document(path)
            redacted, hits = parsed.redact(terms)
            written, warning = index_document_chunks(
                str(document["document_id"]), redacted, settings, replace=True
            )
            db.execute(
                "UPDATE document SET ingest_status = %s, redaction_hits = %s, stats = %s, "
                "updated_at = now() WHERE document_id = %s",
                (
                    "indexed_text_only" if warning else "indexed",
                    json.dumps(hits),
                    json.dumps({
                        **document_stats(redacted),
                        "chunks_written": written,
                        "embedding_warning": warning,
                    }),
                    document["document_id"],
                ),
            )
            print(f"OK      {path.name}  chunks={written}" + (f"  warning={warning}" if warning else ""))
        except Exception as exc:
            exit_code = 1
            print(f"FAILED  {path.name}: {exc}")
    return exit_code


def cmd_eval(args: argparse.Namespace) -> int:
    report = evaluate(args.gold, _settings())
    print(json.dumps(report["summary"], indent=2))
    if args.out:
        path = write_report(report, args.out)
        print(f"report: {path}")
    return 0


def cmd_apps_validate(args: argparse.Namespace) -> int:
    settings = _settings()
    taxonomy = load_taxonomy(settings.taxonomy_path)
    problems = validate_taxonomy(taxonomy, strict=False)
    problems.extend(validate_app_specs(settings.app_specs_dir, taxonomy, strict=False))
    for problem in problems:
        print(f"WARN  {problem}")
    if not problems:
        print("app specs valid")
    if args.strict and problems:
        print("strict mode: failing")
        return 1
    return 0


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppm", description="PPM v2 - template knowledge platform")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="apply migrations").set_defaults(func=cmd_init_db)
    sub.add_parser("status", help="corpus status").set_defaults(func=cmd_status)

    agent_parser = sub.add_parser("agent", help="run the PPM Hermes Agent")
    agent_parser.add_argument("question")
    agent_parser.add_argument("--user", default="senior")
    agent_parser.add_argument("--password")
    agent_parser.add_argument("--thread")
    agent_parser.set_defaults(func=cmd_agent)

    models_parser = sub.add_parser("models", help="model diagnostics")
    models_sub = models_parser.add_subparsers(dest="models_action", required=True)
    models_sub.add_parser("check", help="verify Ollama models").set_defaults(func=cmd_models_check)

    corpus_parser = sub.add_parser("corpus", help="corpus tools")
    corpus_sub = corpus_parser.add_subparsers(dest="corpus_action", required=True)
    report_parser = corpus_sub.add_parser("report", help="corpus inventory and duplicates")
    report_parser.add_argument("--manifest")
    report_parser.add_argument("--json", action="store_true")
    report_parser.add_argument("--no-hash", action="store_true", help="skip duplicate hashing")
    report_parser.set_defaults(func=cmd_corpus_report)
    catalog_parser = corpus_sub.add_parser("catalog", help="import list-of-templates workbooks")
    catalog_parser.add_argument("--dry-run", action="store_true")
    catalog_parser.set_defaults(func=cmd_corpus_catalog)
    corpus_sub.add_parser("redact", help="scrub client names from template titles").set_defaults(
        func=cmd_corpus_redact
    )

    analyst_parser = sub.add_parser("analyst", help="PAT-style project analyst")
    analyst_sub = analyst_parser.add_subparsers(dest="analyst_action", required=True)
    analyst_sub.add_parser("demo", help="run the deterministic cost-benchmark demo").set_defaults(
        func=cmd_analyst_demo
    )
    plan_parser = analyst_sub.add_parser("plan", help="plan a question with the API planner")
    plan_parser.add_argument("question")
    plan_parser.add_argument("--out")
    plan_parser.set_defaults(func=cmd_analyst_plan)
    run_parser = analyst_sub.add_parser("run", help="run an approved plan file")
    run_parser.add_argument("--plan", required=True)
    run_parser.add_argument("--auto", action="store_true")
    run_parser.add_argument("--codegen", choices=["static", "api"], default="static")
    run_parser.add_argument("--access-level", choices=["baseline", "all"], default="all")
    run_parser.set_defaults(func=cmd_analyst_run)
    ask_parser = analyst_sub.add_parser("ask", help="plan and run a question")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--auto", action="store_true")
    ask_parser.add_argument("--codegen", choices=["static", "api"], default="api")
    ask_parser.add_argument("--access-level", choices=["baseline", "all"], default="all")
    ask_parser.set_defaults(func=cmd_analyst_ask)
    status_parser = analyst_sub.add_parser("status", help="show a run, its tasks and artifacts")
    status_parser.add_argument("run_id")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=cmd_analyst_status)
    eval_parser = analyst_sub.add_parser("eval", help="run benchmark questions")
    eval_parser.add_argument("--benchmarks", default=str(REPO_ROOT / "data" / "analyst_benchmarks.yaml"))
    eval_parser.add_argument("--codegen", choices=["api", "static"], default="api")
    eval_parser.add_argument("--out", default=str(REPO_ROOT / "exports" / "eval"))
    eval_parser.set_defaults(func=cmd_analyst_eval)
    teach_parser = analyst_sub.add_parser("teach", help="record a correction and propose a lesson")
    teach_parser.add_argument("run_id")
    teach_parser.add_argument("--note", required=True)
    teach_parser.set_defaults(func=cmd_analyst_teach)
    bench_parser = analyst_sub.add_parser("benchmarks", help="benchmark cases")
    bench_sub = bench_parser.add_subparsers(dest="benchmarks_action", required=True)
    bench_list = bench_sub.add_parser("list")
    bench_list.add_argument("--json", action="store_true")
    bench_list.set_defaults(func=cmd_analyst_benchmarks)

    user_parser = sub.add_parser("user", help="local accounts")
    user_sub = user_parser.add_subparsers(dest="user_action", required=True)
    user_sub.add_parser("seed", help="create the two dev accounts").set_defaults(func=cmd_user_seed)
    user_sub.add_parser("list", help="list accounts").set_defaults(func=cmd_user_list)
    passwd_parser = user_sub.add_parser("passwd", help="change a password")
    passwd_parser.add_argument("username")
    passwd_parser.add_argument("password")
    passwd_parser.set_defaults(func=cmd_user_passwd)
    invite_parser = user_sub.add_parser("invite", help="create an invite code")
    invite_parser.add_argument("--email")
    invite_parser.add_argument("--role", choices=["junior", "senior_manager"], default="junior")
    invite_parser.add_argument("--expires-hours", type=int, default=72)
    invite_parser.set_defaults(func=cmd_user_invite)
    bootstrap_parser = user_sub.add_parser("bootstrap-admin", help="create the first admin")
    bootstrap_parser.add_argument("--username", default="admin")
    bootstrap_parser.add_argument("--display-name", default="Administrator")
    bootstrap_parser.add_argument("--password")
    bootstrap_parser.set_defaults(func=cmd_user_bootstrap)
    revoke_parser = user_sub.add_parser("revoke-sessions", help="log a user out everywhere")
    revoke_parser.add_argument("username")
    revoke_parser.set_defaults(func=cmd_user_revoke)

    memory_parser = sub.add_parser("memory", help="long-term memory")
    memory_sub = memory_parser.add_subparsers(dest="memory_action", required=True)
    memory_list = memory_sub.add_parser("list")
    memory_list.add_argument("--user", required=True)
    memory_list.add_argument("--limit", type=int, default=50)
    memory_list.set_defaults(func=cmd_memory_list)
    memory_clear = memory_sub.add_parser("clear")
    memory_clear.add_argument("--user", required=True)
    memory_clear.set_defaults(func=cmd_memory_clear)
    memory_prune = memory_sub.add_parser("prune")
    memory_prune.add_argument("--days", type=int, default=365)
    memory_prune.set_defaults(func=cmd_memory_prune)

    data_parser = sub.add_parser("data", help="structured project data")
    data_sub = data_parser.add_subparsers(dest="data_action", required=True)
    seed_parser = data_sub.add_parser("seed", help="seed structured data from sample workbooks")
    seed_parser.add_argument("--dry-run", action="store_true")
    seed_parser.set_defaults(func=cmd_data_seed)
    report_data = data_sub.add_parser("report", help="structured data coverage")
    report_data.add_argument("--json", action="store_true")
    report_data.set_defaults(func=cmd_data_report)

    ingest_parser = sub.add_parser("ingest", help="ingest files or the whole manifest")
    ingest_parser.add_argument("paths", nargs="*")
    ingest_parser.add_argument("--manifest")
    ingest_parser.add_argument("--limit", type=int)
    ingest_parser.add_argument("--include-reference", action="store_true",
                               help="also process catalog-only/reference files")
    ingest_parser.set_defaults(func=cmd_ingest)

    review_parser = sub.add_parser("review", help="ingest review queue")
    review_sub = review_parser.add_subparsers(dest="review_action", required=True)
    review_sub.add_parser("list").set_defaults(func=cmd_review, thread=None)
    show_parser = review_sub.add_parser("show")
    show_parser.add_argument("thread")
    show_parser.set_defaults(func=cmd_review)
    approve_parser = review_sub.add_parser("approve")
    approve_parser.add_argument("thread")
    approve_parser.add_argument("--licence")
    approve_parser.add_argument("--confidentiality")
    approve_parser.set_defaults(func=cmd_review)
    reject_parser = review_sub.add_parser("reject")
    reject_parser.add_argument("thread")
    reject_parser.add_argument("--forbid", action="store_true")
    reject_parser.set_defaults(func=cmd_review)

    find_parser = sub.add_parser("find", help="deterministic template search")
    find_parser.add_argument("text", nargs="?")
    find_parser.add_argument("--stage")
    find_parser.add_argument("--category")
    find_parser.add_argument("--format")
    find_parser.add_argument("--collection")
    find_parser.add_argument("--limit", type=int, default=100)
    find_parser.add_argument("--json", action="store_true")
    find_parser.set_defaults(func=cmd_find)

    ask_parser = sub.add_parser("ask", help="cited answer over the corpus")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--collection")
    ask_parser.add_argument("--stage")
    ask_parser.add_argument("--literal", action="store_true", help="skip embeddings")
    ask_parser.add_argument("--json", action="store_true")
    ask_parser.set_defaults(func=cmd_ask)

    reindex_parser = sub.add_parser("reindex", help="rebuild chunks for indexed documents")
    reindex_parser.add_argument("--force", action="store_true")
    reindex_parser.set_defaults(func=cmd_reindex)

    eval_parser = sub.add_parser("eval", help="evaluation harness")
    eval_sub = eval_parser.add_subparsers(dest="eval_action", required=True)
    eval_ask = eval_sub.add_parser("ask")
    eval_ask.add_argument("--gold", default=str(REPO_ROOT / "data" / "gold_questions.yaml"))
    eval_ask.add_argument("--out", default=str(REPO_ROOT / "exports" / "eval"))
    eval_ask.set_defaults(func=cmd_eval)

    apps_parser = sub.add_parser("apps", help="app spec validation")
    apps_sub = apps_parser.add_subparsers(dest="apps_action", required=True)
    validate_parser = apps_sub.add_parser("validate")
    validate_parser.add_argument("--strict", action="store_true")
    validate_parser.set_defaults(func=cmd_apps_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
