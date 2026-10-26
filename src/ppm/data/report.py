"""Coverage report for structured project data."""

from __future__ import annotations

from typing import Any

from ppm import db

TABLES = [
    "project",
    "cost_plan",
    "cost_line",
    "benchmark_rate",
    "risk",
    "change_event",
    "deliverable",
]


def build_report() -> dict[str, Any]:
    counts = {
        table: db.fetch_one(f"SELECT count(*) AS count FROM {table}")["count"]
        for table in TABLES
    }
    currencies = db.fetch_all(
        "SELECT currency, count(*) AS count FROM cost_plan "
        "WHERE currency IS NOT NULL GROUP BY currency ORDER BY count DESC"
    )
    data_classes = db.fetch_all(
        "SELECT data_class, count(*) AS count FROM project GROUP BY data_class ORDER BY data_class"
    )
    dates = db.fetch_one(
        "SELECT min(plan_date) AS first_plan, max(plan_date) AS last_plan FROM cost_plan"
    )
    missing = db.fetch_one(
        "SELECT "
        "(SELECT count(*) FROM project WHERE gfa_m2 IS NULL) AS projects_without_gfa, "
        "(SELECT count(*) FROM project WHERE area_basis IS NULL) AS projects_without_basis, "
        "(SELECT count(*) FROM cost_line WHERE source_ref IS NULL OR source_ref = '') AS lines_without_source, "
        "(SELECT count(*) FROM risk WHERE likelihood IS NULL OR impact IS NULL) AS risks_without_rating, "
        "(SELECT count(*) FROM change_event WHERE cost_impact IS NULL) AS changes_without_cost"
    )
    top_projects = db.fetch_all(
        "SELECT p.name, cp.currency, cp.total_cost, cp.gfa_m2, cp.plan_date "
        "FROM cost_plan cp JOIN project p ON p.project_id = cp.project_id "
        "ORDER BY cp.total_cost DESC NULLS LAST LIMIT 10"
    )
    reconciliation = db.fetch_one(
        "SELECT count(*) AS mismatched FROM ("
        "  SELECT cp.costplan_id, cp.total_cost, COALESCE(SUM(cl.amount), 0) AS line_total "
        "  FROM cost_plan cp LEFT JOIN cost_line cl ON cl.costplan_id = cp.costplan_id "
        "  WHERE cp.total_cost IS NOT NULL "
        "  GROUP BY cp.costplan_id, cp.total_cost "
        "  HAVING ABS(cp.total_cost - COALESCE(SUM(cl.amount), 0)) > (ABS(cp.total_cost) * 0.005)"
        ") AS bad"
    )["mismatched"]
    risk_stats = db.fetch_one(
        "SELECT count(*) AS count, min(score) AS min_score, max(score) AS max_score, "
        "round(avg(score)::numeric, 2) AS avg_score FROM risk"
    )
    change_stats = db.fetch_one(
        "SELECT count(*) AS count, COALESCE(sum(cost_impact), 0) AS total_cost, "
        "COALESCE(sum(time_impact_days), 0) AS total_days FROM change_event"
    )
    return {
        "counts": counts,
        "currencies": currencies,
        "data_classes": data_classes,
        "dates": dates,
        "missing": missing,
        "top_projects": top_projects,
        "reconciliation_mismatches": reconciliation,
        "risk": risk_stats,
        "change": change_stats,
    }


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Structured project data")
    lines.append("")
    for table, count in report["counts"].items():
        lines.append(f"  {table:<18} {count}")
    lines.append("")
    lines.append("cost plan currencies: " + (
        ", ".join(f"{row['currency']}={row['count']}" for row in report["currencies"]) or "none"
    ))
    lines.append("project data classes: " + (
        ", ".join(f"{row['data_class']}={row['count']}" for row in report["data_classes"]) or "none"
    ))
    dates = report["dates"] or {}
    lines.append(f"plan dates: {dates.get('first_plan')} .. {dates.get('last_plan')}")
    lines.append("")
    missing = report["missing"] or {}
    lines.append("missing / quality:")
    for key, value in missing.items():
        lines.append(f"  {key:<26} {value}")
    lines.append(f"  {'reconcile_mismatches':<26} {report['reconciliation_mismatches']}")
    lines.append("")
    lines.append("top projects by total cost:")
    if not report["top_projects"]:
        lines.append("  (none)")
    for row in report["top_projects"]:
        gfa = f"{row['gfa_m2']:.0f} m2" if row["gfa_m2"] else "no GFA"
        lines.append(
            f"  {row['name']:<38} {row['currency'] or '?'} "
            f"{(row['total_cost'] or 0):,.0f}  {gfa}  {row['plan_date'] or ''}"
        )
    lines.append("")
    risk = report["risk"] or {}
    lines.append(
        f"risk: count={risk.get('count')} min={risk.get('min_score')} "
        f"max={risk.get('max_score')} avg={risk.get('avg_score')}"
    )
    change = report["change"] or {}
    lines.append(
        f"change: count={change.get('count')} total_cost_impact={change.get('total_cost')} "
        f"total_days={change.get('total_days')}"
    )
    return "\n".join(lines)
